/**
 * PlatPursuit.Games.Driver.TrackGenerator
 *
 * Procedural track generation for Stellar Circuit.
 *
 * This module has two responsibilities:
 * 1. GENERATION (pure math): Take a seed string and difficulty level,
 *    produce a TrackData object with centerline, edges, checkpoints, etc.
 * 2. RENDERING (Phaser visuals): Take a TrackData object and draw it
 *    onto a RenderTexture for efficient display.
 *
 * These are separated because generation is reusable (minimap, ghost
 * validation, track preview) while rendering is Phaser-specific.
 *
 * Key algorithms:
 * - Mulberry32 seeded PRNG for deterministic generation
 * - Catmull-Rom spline interpolation for smooth closed-loop curves
 * - Curvature-based track width variation
 * - Arc-length-based checkpoint placement
 * - Nearest-segment boundary test for on/off-track detection
 */

window.PlatPursuit = window.PlatPursuit || {};
window.PlatPursuit.Games = window.PlatPursuit.Games || {};
window.PlatPursuit.Games.Driver = window.PlatPursuit.Games.Driver || {};

(function() {
    'use strict';

    // ===================================================================
    // SEEDED RANDOM NUMBER GENERATOR
    // ===================================================================
    //
    // Why we need this:
    // Math.random() is NOT seedable. You can't say "start from state X
    // and give me the same sequence every time." But we need exactly that:
    // seed "hello" must ALWAYS produce the same track, on every browser,
    // every device, every time. That's what makes daily challenges and
    // ghost racing possible (everyone races the same track).
    //
    // Algorithm: Mulberry32
    // A 32-bit PRNG that stores one integer as state. Each call scrambles
    // the state using bit shifts, XOR, and multiplication (chosen to
    // produce well-distributed output). The result is divided by 2^32
    // to get a float in [0, 1). It passes SmallCrush statistical tests,
    // meaning the output is "random enough" for game use.

    /**
     * Converts a string seed into a 32-bit integer for the PRNG.
     *
     * Uses the classic "hash * 31 + char" approach. Why 31?
     * - It's prime, which distributes hash values well
     * - (hash << 5) - hash is the same as hash * 31, but uses only
     *   bit shift and subtraction (faster than multiplication on some CPUs)
     * - This is the same algorithm Java uses for String.hashCode()
     *
     * The `hash & hash` line isn't a no-op: in JavaScript, bitwise AND
     * converts the number to a 32-bit signed integer. Without this,
     * repeated shifts could produce numbers beyond 32-bit range, and
     * JavaScript would silently convert to floating point (losing the
     * bit-level precision our PRNG needs).
     *
     * @param {string} str - The seed string to hash
     * @returns {number} An unsigned 32-bit integer
     */
    function hashString(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            const char = str.charCodeAt(i);
            hash = ((hash << 5) - hash) + char;
            hash = hash & hash;  // Force 32-bit integer
        }
        // Use unsigned right shift instead of Math.abs to avoid
        // collisions: Math.abs(-1000) === Math.abs(1000), but
        // (-1000 >>> 0) !== (1000 >>> 0). Also avoids the edge
        // case where Math.abs(INT32_MIN) overflows back to negative
        // when coerced via |0.
        return hash >>> 0;
    }

    /**
     * Mulberry32 seeded pseudo-random number generator.
     *
     * Lifecycle:
     *   const rng = new SeededRandom("my-seed");
     *   const val1 = rng.next();      // Always the same for "my-seed"
     *   const val2 = rng.next();      // Always the same second value
     *   const track = generateTrack(rng);  // Deterministic track
     *
     * The sequence is deterministic: calling next() 1000 times from
     * the same seed always produces the same 1000 values in the same
     * order. This is what makes "same seed = same track" work.
     */
    class SeededRandom {
        /**
         * @param {string|number} seed - Either a string (hashed to integer)
         *   or a number (used directly). Strings are more user-friendly
         *   ("daily-2026-02-16"), numbers are useful for internal retries
         *   (if track validation fails, try seed+1).
         */
        constructor(seed) {
            if (typeof seed === 'string') {
                this.state = hashString(seed);
            } else {
                this.state = seed | 0;  // Ensure 32-bit integer
            }

            // Edge case: a seed of 0 would produce all zeros.
            // Adding 1 avoids this degenerate case.
            if (this.state === 0) this.state = 1;
        }

        /**
         * Returns the next pseudo-random float in [0, 1).
         *
         * The Mulberry32 algorithm in 5 steps:
         * 1. Add a large odd constant (the "increment")
         * 2. XOR-shift and multiply to scramble bits
         * 3. XOR-shift and multiply again (second round of mixing)
         * 4. Final XOR-shift for output whitening
         * 5. Convert unsigned 32-bit integer to float via division
         *
         * The magic constants (0x6D2B79F5, etc.) were chosen by the
         * algorithm's designer through empirical testing to maximize
         * randomness quality. They're not arbitrary: different constants
         * produce measurably worse statistical properties.
         *
         * @returns {number} Float in [0, 1)
         */
        next() {
            // Step 1: Increment state by a large odd constant.
            // The |0 forces 32-bit integer (handles overflow correctly).
            this.state = (this.state + 0x6D2B79F5) | 0;

            // Step 2-4: Bit mixing. Each line XOR-shifts the value and
            // multiplies by an odd number. This spreads the entropy from
            // the increment across all 32 bits.
            let t = Math.imul(this.state ^ (this.state >>> 15), 1 | this.state);
            t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;

            // Step 5: Final XOR-shift, then convert to unsigned (>>> 0)
            // and divide by 2^32 to get [0, 1) float.
            return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
        }

        /**
         * Returns a float in [min, max).
         * Convenience wrapper: scales next() to the desired range.
         */
        range(min, max) {
            return min + this.next() * (max - min);
        }

        /**
         * Returns an integer in [min, max] (inclusive on both ends).
         *
         * Why max + 1? Because next() returns [0, 1), multiplying by
         * (max - min + 1) gives [0, max-min+1), and Math.floor converts
         * to [0, max-min] (integers). Adding min shifts to [min, max].
         */
        intRange(min, max) {
            return Math.floor(this.range(min, max + 1));
        }
    }

    // ===================================================================
    // TRACK GENERATION CONSTANTS
    // ===================================================================

    // Spline sampling resolution: how many points per control-point segment.
    // 40 samples per segment with 10 control points = 400 total points
    // defining the track centerline. Higher = smoother but more memory.
    // Increased from 30 to 40 to keep curves visually smooth at the larger
    // track scale (each sample covers more world distance with bigger radii).
    const SAMPLES_PER_SEGMENT = 40;

    // Minimum distance between adjacent control points (in pixels).
    // Prevents "pinch points" where two points are so close the track
    // would fold back on itself or create an impossibly tight turn.
    // Increased from 200 to 350 to match the larger track scale and
    // reduce the frequency of extreme tight turns that cause edge overlap.
    const MIN_POINT_DISTANCE = 350;

    // Track width bounds (in pixels). Width varies with curvature:
    // straights are wide (easy to go fast), curves are narrow (technical).
    // Increased from 90/220 to 140/320 to give more room for the drifty
    // space physics. Players need width to express momentum management.
    const TRACK_WIDTH_MIN = 140;
    const TRACK_WIDTH_MAX = 320;

    // Width smoothing window: how many samples to average when smoothing
    // the width transitions. Prevents jarring width changes.
    const WIDTH_SMOOTHING_WINDOW = 10;

    // Checkpoint count range. More checkpoints = more validation points
    // that the player must pass through each lap.
    const MIN_CHECKPOINTS = 8;
    const MAX_CHECKPOINTS = 12;

    // Checkpoint curvature avoidance: checkpoints placed where curvature
    // exceeds this threshold are nudged to the nearest low-curvature point.
    // 0.35 is above gentle curves (~0.1-0.2) but below the sharpest turns
    // (~0.7-1.0), catching the range where perpendicular gates look awkward
    // and the track is at its narrowest.
    const CHECKPOINT_CURVATURE_THRESHOLD = 0.35;

    // Minimum distance (in centerline samples) between any two checkpoints
    // after curvature nudging. With 320-520 total samples and 8-12
    // checkpoints, even spacing is ~30-65 samples apart, so 15 is a safe
    // floor that only triggers in extreme nudge cases.
    const MIN_CHECKPOINT_SAMPLE_DISTANCE = 15;

    // Boost pad placement rules.
    const BOOST_CURVATURE_THRESHOLD = 0.15;  // Only on straight-ish sections
    const MIN_BOOST_SPACING_RATIO = 0.15;    // Min 15% of track between pads
    const MIN_BOOST_PADS = 3;
    const MAX_BOOST_PADS = 5;

    // Boost pad lookahead: when evaluating a candidate position, also check
    // this many samples AHEAD for upcoming curves. Prevents placing boosts
    // at the end of straightaways right before a sharp turn (which would
    // feel terrible: you get speed-boosted into an impossible corner).
    const BOOST_LOOKAHEAD = 45;

    // The lookahead uses a SEPARATE, higher curvature threshold than the
    // position check. The position itself must be straight-ish (< 0.15),
    // but the lookahead only rejects if a genuinely SHARP turn is ahead
    // (>= 0.4). Gentle curves ahead are fine: the player can handle a
    // boost going into a sweeping turn. It's the hairpins we want to avoid.
    const BOOST_LOOKAHEAD_THRESHOLD = 0.4;

    // How many segments ahead to check for edge self-intersection when
    // clipping loops at render time. Loops on the inside edge of tight
    // corners are typically 5-20 segments long. 50 is conservative.
    const LOOP_SCAN_WINDOW = 50;

    // Padding around track edges for world bounds calculation.
    // Gives room for starfield background beyond the track.
    const BOUNDS_PADDING = 300;

    // ===================================================================
    // TRACK GENERATION: CONTROL POINTS
    // ===================================================================

    /**
     * Generates the control points that define the track's skeleton.
     *
     * The algorithm works by placing points around a distorted ellipse:
     *
     * 1. Start with a base ellipse (center at origin)
     * 2. Space points evenly around the ellipse by angle
     * 3. Perturb each point's angle (shift it around the ellipse)
     * 4. Perturb each point's radius (push it closer/farther from center)
     * 5. Enforce minimum distance between neighbors
     * 6. Shift all points so the centroid is at (0, 0)
     *
     * Why an ellipse and not random points? An ellipse naturally forms
     * a loop (which is what a race circuit is). By perturbing the points,
     * we get interesting shapes while guaranteeing the track doesn't
     * cross itself (as long as perturbations are reasonable).
     *
     * @param {SeededRandom} rng - The seeded random generator
     * @returns {Array<{x: number, y: number}>} Control points in order
     */
    function generateControlPoints(rng) {
        // Number of control points determines track complexity.
        // 8 points = simple oval-ish track, 13 = complex twisty track.
        const numPoints = rng.intRange(8, 13);

        // Base ellipse dimensions. Not a circle because asymmetry makes
        // tracks more interesting (some sections are stretched, others compressed).
        // Doubled from 800-1200/600-1000 to create ~2x track length.
        // Circumference scales linearly with radius, so 2x radii = 2x lap length.
        // This gives races a more substantial feel instead of "go-kart track."
        const radiusX = rng.range(1600, 2400);
        const radiusY = rng.range(1200, 2000);

        // Generate points around the ellipse
        const points = [];
        for (let i = 0; i < numPoints; i++) {
            // Base angle: evenly spaced around the circle.
            // 2*PI / numPoints gives equal angular spacing.
            const baseAngle = (2 * Math.PI * i) / numPoints;

            // Angular perturbation: shift the point around the ellipse.
            // This breaks the even spacing, creating longer and shorter
            // segments. +/- 0.25 radians (~14 degrees) is enough to create
            // variety without risking points swapping order.
            const anglePerturbation = rng.range(-0.25, 0.25);
            const angle = baseAngle + anglePerturbation;

            // Radial perturbation: push the point closer to or farther from
            // the center. 0.7 = 30% closer, 1.3 = 30% farther.
            // This creates the interesting bumps and indentations in the
            // track shape. Without this, every track would be an ellipse.
            const radiusMult = rng.range(0.7, 1.3);

            points.push({
                x: Math.cos(angle) * radiusX * radiusMult,
                y: Math.sin(angle) * radiusY * radiusMult,
            });
        }

        // Enforce minimum distance between adjacent points.
        // If two neighbors are too close, the spline between them would
        // create an impossibly tight turn. We push the closer point outward
        // (away from center) to create more space.
        //
        // Multiple passes: pushing point N outward can create a new
        // violation with point N+1, and the wrap-around (last point vs
        // first) is never rechecked in a single pass. We run up to 3
        // passes, stopping early if no violations remain.
        for (let pass = 0; pass < 3; pass++) {
            let anyViolation = false;
            for (let i = 0; i < points.length; i++) {
                const next = (i + 1) % points.length;
                const dx = points[next].x - points[i].x;
                const dy = points[next].y - points[i].y;
                const dist = Math.sqrt(dx * dx + dy * dy);

                if (dist < MIN_POINT_DISTANCE) {
                    // Push the next point outward from center by the deficit
                    const pointAngle = Math.atan2(points[next].y, points[next].x);
                    const pushDist = (MIN_POINT_DISTANCE - dist) / 2 + 20;
                    points[next].x += Math.cos(pointAngle) * pushDist;
                    points[next].y += Math.sin(pointAngle) * pushDist;
                    anyViolation = true;
                }
            }
            if (!anyViolation) break;
        }

        // Normalize: shift all points so the centroid is at (0, 0).
        // This centers the track in the world, making camera and bounds
        // calculations simpler.
        let cx = 0, cy = 0;
        for (const p of points) {
            cx += p.x;
            cy += p.y;
        }
        cx /= points.length;
        cy /= points.length;
        for (const p of points) {
            p.x -= cx;
            p.y -= cy;
        }

        return points;
    }

    // ===================================================================
    // TRACK GENERATION: CATMULL-ROM SPLINE
    // ===================================================================

    /**
     * Evaluates a point on a Catmull-Rom spline segment.
     *
     * Catmull-Rom is a type of cubic spline that passes THROUGH its
     * control points (unlike Bezier curves, which are "attracted" toward
     * control points but don't necessarily touch them). This makes it
     * perfect for track generation: the control points ARE on the track.
     *
     * How it works:
     * Given 4 points (P0, P1, P2, P3), the spline interpolates between
     * P1 and P2. P0 and P3 are "tangent guides": they influence the
     * curve's direction at P1 and P2 but the curve doesn't pass through them.
     *
     * The parameter t goes from 0 to 1:
     *   t=0 gives exactly P1
     *   t=1 gives exactly P2
     *   t=0.5 gives a point halfway along the curve (NOT the midpoint
     *          of the straight line P1-P2, but the midpoint of the CURVE)
     *
     * The matrix coefficients (0.5 * [-1,3,-3,1], etc.) come from the
     * Catmull-Rom basis matrix. They're derived from the constraint that
     * the tangent at P1 should be parallel to the line P0-P2 (and
     * similarly at P2 parallel to P1-P3). This creates smooth transitions
     * between segments.
     *
     * @param {{x,y}} p0 - Tangent guide before the segment
     * @param {{x,y}} p1 - Segment start point (returned when t=0)
     * @param {{x,y}} p2 - Segment end point (returned when t=1)
     * @param {{x,y}} p3 - Tangent guide after the segment
     * @param {number} t  - Parameter [0, 1] along the segment
     * @returns {{x: number, y: number}} Interpolated point
     */
    function catmullRom(p0, p1, p2, p3, t) {
        const t2 = t * t;
        const t3 = t2 * t;

        // These four coefficients are the Catmull-Rom basis functions
        // evaluated at parameter t. Each one weights one control point.
        //
        // At t=0: c0=0, c1=1, c2=0, c3=0 → returns P1
        // At t=1: c0=0, c1=0, c2=1, c3=0 → returns P2
        // At t=0.5: all four contribute, creating the smooth curve
        const c0 = -0.5 * t3 + t2 - 0.5 * t;
        const c1 =  1.5 * t3 - 2.5 * t2 + 1;
        const c2 = -1.5 * t3 + 2.0 * t2 + 0.5 * t;
        const c3 =  0.5 * t3 - 0.5 * t2;

        return {
            x: c0 * p0.x + c1 * p1.x + c2 * p2.x + c3 * p3.x,
            y: c0 * p0.y + c1 * p1.y + c2 * p2.y + c3 * p3.y,
        };
    }

    /**
     * Interpolates all control points into a dense polyline using
     * Catmull-Rom spline segments.
     *
     * For a CLOSED loop, each segment uses 4 control points with wrapping:
     *   Segment i uses: P[i-1], P[i], P[i+1], P[i+2]  (all mod N)
     *
     * The result is a smooth closed curve that passes through every
     * control point. With SAMPLES_PER_SEGMENT=30 and 10 control points,
     * we get 300 sample points defining the track centerline.
     *
     * @param {Array<{x,y}>} controlPoints - The track skeleton points
     * @returns {Array<{x: number, y: number}>} Dense polyline (centerline)
     */
    function interpolateSpline(controlPoints) {
        const n = controlPoints.length;
        const centerPoints = [];

        for (let i = 0; i < n; i++) {
            // The 4 points for this segment, wrapping around for closed loop
            const p0 = controlPoints[(i - 1 + n) % n];
            const p1 = controlPoints[i];
            const p2 = controlPoints[(i + 1) % n];
            const p3 = controlPoints[(i + 2) % n];

            // Sample SAMPLES_PER_SEGMENT points along this segment.
            // We don't include t=1.0 because the next segment starts there
            // (its t=0.0 is this segment's t=1.0). Avoids duplicate points.
            for (let s = 0; s < SAMPLES_PER_SEGMENT; s++) {
                const t = s / SAMPLES_PER_SEGMENT;
                centerPoints.push(catmullRom(p0, p1, p2, p3, t));
            }
        }

        return centerPoints;
    }

    // ===================================================================
    // TRACK GENERATION: CURVATURE & WIDTH
    // ===================================================================

    /**
     * Calculates the curvature at each point along the centerline.
     *
     * Curvature measures "how sharply the track is turning" at each point.
     * Straight sections have curvature ~0, tight turns have high curvature.
     *
     * The formula uses three consecutive points to approximate curvature:
     *
     *   curvature = |cross product| / (length1 * length2)
     *
     * Why this works:
     * - The cross product of two vectors gives the area of their
     *   parallelogram. If the vectors point in nearly the same direction
     *   (straight track), the area is ~0. If they're at a sharp angle
     *   (tight turn), the area is large.
     * - Dividing by the lengths normalizes for segment length, so
     *   curvature measures the angle change, not the segment size.
     *
     * The values are normalized to [0, 1] range by dividing by the
     * maximum curvature found. This makes them easy to use for width
     * mapping: 0 = straightest section, 1 = sharpest turn.
     *
     * @param {Array<{x,y}>} points - Track centerline points
     * @returns {Array<number>} Normalized curvature at each point [0, 1]
     */
    function calculateCurvatures(points) {
        const n = points.length;
        const curvatures = new Array(n);
        let maxCurvature = 0;

        for (let i = 0; i < n; i++) {
            const prev = points[(i - 1 + n) % n];
            const curr = points[i];
            const next = points[(i + 1) % n];

            // Vectors from curr to its neighbors
            const dx1 = curr.x - prev.x;
            const dy1 = curr.y - prev.y;
            const dx2 = next.x - curr.x;
            const dy2 = next.y - curr.y;

            // Cross product magnitude: how much the direction changes
            const cross = Math.abs(dx1 * dy2 - dy1 * dx2);

            // Lengths of both vectors
            const len1 = Math.sqrt(dx1 * dx1 + dy1 * dy1);
            const len2 = Math.sqrt(dx2 * dx2 + dy2 * dy2);

            // Curvature = direction change / distance
            // Guard against division by zero (coincident points)
            if (len1 < 0.001 || len2 < 0.001) {
                curvatures[i] = 0;
            } else {
                curvatures[i] = cross / (len1 * len2);
            }

            if (curvatures[i] > maxCurvature) {
                maxCurvature = curvatures[i];
            }
        }

        // Normalize to [0, 1] range
        if (maxCurvature > 0) {
            for (let i = 0; i < n; i++) {
                curvatures[i] /= maxCurvature;
            }
        }

        return curvatures;
    }

    /**
     * Maps curvature values to track widths with smoothing.
     *
     * The mapping is: high curvature (sharp turns) = narrow track,
     * low curvature (straights) = wide track. This creates natural
     * difficulty variation: players can go fast on wide straights but
     * need precision on narrow curves.
     *
     * The smoothstep function (t * t * (3 - 2t)) creates a pleasing
     * S-curve transition instead of a linear mapping. This means:
     * - Gentle curves stay relatively wide (forgiving)
     * - Only sharp curves get really narrow (challenging)
     * - The transition between wide and narrow is smooth, not abrupt
     *
     * After the initial mapping, a moving average smooths out any
     * remaining sharp width transitions. The track width should flow
     * gradually, not jump between wide and narrow.
     *
     * @param {Array<number>} curvatures - Normalized curvature [0, 1]
     * @returns {Array<number>} Track width at each point (pixels)
     */
    function calculateWidths(curvatures) {
        const n = curvatures.length;
        const rawWidths = new Array(n);

        // Initial mapping: curvature → width via smoothstep
        for (let i = 0; i < n; i++) {
            const c = curvatures[i];
            // Smoothstep: S-curve that maps [0,1] to [0,1] smoothly
            const t = c * c * (3 - 2 * c);
            // Invert: high curvature = narrow (low width)
            rawWidths[i] = TRACK_WIDTH_MAX - (TRACK_WIDTH_MAX - TRACK_WIDTH_MIN) * t;
        }

        // Smoothing pass: moving average to prevent abrupt width changes.
        // Uses a circular buffer approach since the track is a closed loop.
        const smoothed = new Array(n);
        const halfWindow = Math.floor(WIDTH_SMOOTHING_WINDOW / 2);

        for (let i = 0; i < n; i++) {
            let sum = 0;
            let count = 0;
            for (let j = -halfWindow; j <= halfWindow; j++) {
                sum += rawWidths[(i + j + n) % n];
                count++;
            }
            smoothed[i] = sum / count;
        }

        return smoothed;
    }

    /**
     * Generates the left and right edge points from centerline + widths.
     *
     * At each centerline point, we find the perpendicular direction (the
     * "normal") and offset left and right by half the track width. The
     * perpendicular is found by rotating the tangent direction 90 degrees.
     *
     * How perpendicular direction works:
     * If the track is going in direction (dx, dy), the perpendicular
     * is (-dy, dx). This is a 90-degree counter-clockwise rotation.
     * (You can verify: if (dx,dy) = (1,0) i.e. going right, then
     * (-dy,dx) = (0,1) i.e. pointing up, which is indeed perpendicular.)
     *
     * Self-intersection note:
     * On tight corners, the inside edge can fold back on itself when
     * halfWidth exceeds the local radius of curvature. This is handled
     * at render time: strokeEdgeWithLoopClip() detects where the edge
     * polyline crosses itself and skips the looped section of the stroke.
     * The surface fill doesn't need clipping because overlapping quads
     * are visually identical to non-overlapping ones (same fill color).
     *
     * @param {Array<{x,y}>} centerPoints - Track centerline
     * @param {Array<number>} widths - Track width at each point
     * @returns {{leftEdge: Array, rightEdge: Array, directions: Array}}
     */
    function generateEdgePoints(centerPoints, widths) {
        const n = centerPoints.length;
        const leftEdge = new Array(n);
        const rightEdge = new Array(n);
        const directions = new Array(n);

        // --- Pass 1: Compute tangent directions ---
        for (let i = 0; i < n; i++) {
            const next = (i + 1) % n;
            const dx = centerPoints[next].x - centerPoints[i].x;
            const dy = centerPoints[next].y - centerPoints[i].y;
            const len = Math.sqrt(dx * dx + dy * dy);

            directions[i] = {
                x: len > 0 ? dx / len : 1,
                y: len > 0 ? dy / len : 0,
            };
        }

        // --- Pass 2: Perpendicular offset ---
        // Offset by halfWidth along the normal at each point. On tight
        // corners, the inside edge may self-intersect, but this is handled
        // at render time by strokeEdgeWithLoopClip().
        for (let i = 0; i < n; i++) {
            const nx = -directions[i].y;
            const ny = directions[i].x;
            const halfWidth = widths[i] / 2;

            leftEdge[i] = {
                x: centerPoints[i].x + nx * halfWidth,
                y: centerPoints[i].y + ny * halfWidth,
            };
            rightEdge[i] = {
                x: centerPoints[i].x - nx * halfWidth,
                y: centerPoints[i].y - ny * halfWidth,
            };
        }

        return { leftEdge, rightEdge, directions };
    }

    // ===================================================================
    // TRACK GENERATION: CHECKPOINTS & BOOST PADS
    // ===================================================================

    /**
     * Places checkpoints at even arc-length intervals around the track.
     *
     * Why arc-length instead of even sample indices?
     * Sample indices are evenly spaced in PARAMETER space (t values),
     * but not necessarily in DISTANCE space. A tight curve has many
     * samples close together while a long straight has samples far apart.
     * Arc-length spacing ensures checkpoints are evenly spaced in actual
     * DISTANCE along the track, which feels fair to the player.
     *
     * Arc length is calculated by summing the distances between consecutive
     * centerline points. Then we divide the total length into equal
     * segments and place a checkpoint at each division point.
     *
     * Checkpoint 0 is always the start/finish line.
     *
     * @param {Array<{x,y}>} centerPoints - Track centerline
     * @param {Array<{x,y}>} leftEdge - Left boundary points
     * @param {Array<{x,y}>} rightEdge - Right boundary points
     * @param {Array<{x,y}>} directions - Tangent directions
     * @param {Array<number>} curvatures - Normalized curvature [0,1] at each sample
     * @param {SeededRandom} rng - For checkpoint count
     * @returns {{checkpoints: Array, totalArcLength: number}}
     */
    function placeCheckpoints(centerPoints, leftEdge, rightEdge, directions, curvatures, rng) {
        const n = centerPoints.length;

        // Step 1: Calculate cumulative arc length at each sample point.
        // arcLengths[i] = total distance from point 0 to point i along
        // the centerline. arcLengths[0] = 0 by definition.
        const arcLengths = new Array(n);
        arcLengths[0] = 0;
        let totalArcLength = 0;

        for (let i = 1; i < n; i++) {
            const dx = centerPoints[i].x - centerPoints[i - 1].x;
            const dy = centerPoints[i].y - centerPoints[i - 1].y;
            totalArcLength += Math.sqrt(dx * dx + dy * dy);
            arcLengths[i] = totalArcLength;
        }

        // Add the closing segment (last point back to first point)
        const dxClose = centerPoints[0].x - centerPoints[n - 1].x;
        const dyClose = centerPoints[0].y - centerPoints[n - 1].y;
        totalArcLength += Math.sqrt(dxClose * dxClose + dyClose * dyClose);

        // Step 2: Determine checkpoint count and spacing
        const numCheckpoints = rng.intRange(MIN_CHECKPOINTS, MAX_CHECKPOINTS);
        const spacing = totalArcLength / numCheckpoints;

        // Step 3: Place checkpoints at even arc-length intervals
        const checkpoints = [];
        for (let c = 0; c < numCheckpoints; c++) {
            const targetArc = c * spacing;

            // Binary search for the sample index closest to this arc length.
            // arcLengths is sorted (monotonically increasing), so binary
            // search is efficient: O(log n) instead of O(n).
            let lo = 0, hi = n - 1;
            while (lo < hi) {
                const mid = (lo + hi) >> 1;  // Bitwise right shift = floor(div 2)
                if (arcLengths[mid] < targetArc) {
                    lo = mid + 1;
                } else {
                    hi = mid;
                }
            }

            // Curvature-aware nudge: if this position sits on a tight
            // turn, scan both directions along the closed-loop centerline
            // for the nearest point where curvature is acceptable.
            let finalIndex = lo;

            if (curvatures[lo] > CHECKPOINT_CURVATURE_THRESHOLD) {
                // Scan forward (increasing index, wrapping at n)
                let fwd = -1;
                for (let step = 1; step < n; step++) {
                    const idx = (lo + step) % n;
                    if (curvatures[idx] <= CHECKPOINT_CURVATURE_THRESHOLD) {
                        fwd = step;
                        break;
                    }
                }

                // Scan backward (decreasing index, wrapping at 0)
                let bwd = -1;
                for (let step = 1; step < n; step++) {
                    const idx = (lo - step + n) % n;
                    if (curvatures[idx] <= CHECKPOINT_CURVATURE_THRESHOLD) {
                        bwd = step;
                        break;
                    }
                }

                // Pick whichever direction found a valid point closer
                let candidateIndex = lo;
                if (fwd >= 0 && bwd >= 0) {
                    candidateIndex = fwd <= bwd
                        ? (lo + fwd) % n
                        : (lo - bwd + n) % n;
                } else if (fwd >= 0) {
                    candidateIndex = (lo + fwd) % n;
                } else if (bwd >= 0) {
                    candidateIndex = (lo - bwd + n) % n;
                }

                // Overlap check: ensure the candidate isn't too close
                // to any already-placed checkpoint (circular distance)
                let overlaps = false;
                for (const placed of checkpoints) {
                    let dist = Math.abs(candidateIndex - placed.index);
                    dist = Math.min(dist, n - dist);
                    if (dist < MIN_CHECKPOINT_SAMPLE_DISTANCE) {
                        overlaps = true;
                        break;
                    }
                }

                // If overlap, keep the original position. A checkpoint
                // on a turn beats skipping one or clustering two together.
                finalIndex = overlaps ? lo : candidateIndex;
            }

            checkpoints.push({
                index: finalIndex,
                position: { x: centerPoints[finalIndex].x, y: centerPoints[finalIndex].y },
                leftPoint: { x: leftEdge[finalIndex].x, y: leftEdge[finalIndex].y },
                rightPoint: { x: rightEdge[finalIndex].x, y: rightEdge[finalIndex].y },
                direction: { x: directions[finalIndex].x, y: directions[finalIndex].y },
            });
        }

        return { checkpoints, totalArcLength };
    }

    /**
     * Places boost pads on straight sections of the track.
     *
     * Rules:
     * 1. Only on sections where curvature is low NOW and for the next
     *    BOOST_LOOKAHEAD samples (prevents boosts right before turns)
     * 2. Minimum spacing between pads (prevents clustering)
     * 3. Not too close to checkpoints (avoids visual clutter)
     *
     * The algorithm:
     * 1. Find all candidate positions (low curvature with lookahead)
     * 2. Shuffle candidates (using seeded RNG for determinism)
     * 3. Greedily select pads, rejecting any too close to already-placed ones
     *
     * @param {Array<{x,y}>} centerPoints - Track centerline
     * @param {Array<number>} curvatures - Normalized curvature values
     * @param {Array<{x,y}>} directions - Tangent directions
     * @param {Array<number>} widths - Track widths
     * @param {number} totalSamples - Total number of centerline samples
     * @param {SeededRandom} rng - For pad count and shuffling
     * @returns {Array} Boost pad data objects
     */
    function placeBoostPads(centerPoints, curvatures, directions, widths, totalSamples, rng) {
        const targetCount = rng.intRange(MIN_BOOST_PADS, MAX_BOOST_PADS);
        const minSpacing = Math.floor(totalSamples * MIN_BOOST_SPACING_RATIO);

        // Step 1: Find candidate positions.
        // Two checks per candidate:
        //   a) The position itself must be straight-ish (curvature < 0.15)
        //   b) No SHARP turn (curvature >= 0.4) in the next 45 samples ahead
        // This allows boosts before gentle curves (the player can handle
        // those) while preventing boosts before hairpin turns.
        const candidates = [];
        for (let i = 0; i < totalSamples; i++) {
            // Check a: position must be on a straight section
            if (curvatures[i] >= BOOST_CURVATURE_THRESHOLD) continue;

            // Check b: no sharp turn in the lookahead window
            let hasSharpTurnAhead = false;
            for (let look = 1; look <= BOOST_LOOKAHEAD; look++) {
                if (curvatures[(i + look) % totalSamples] >= BOOST_LOOKAHEAD_THRESHOLD) {
                    hasSharpTurnAhead = true;
                    break;
                }
            }
            if (!hasSharpTurnAhead) {
                candidates.push(i);
            }
        }

        // Step 2: Shuffle candidates using Fisher-Yates (seeded)
        // Fisher-Yates produces a uniformly random permutation.
        // We use it instead of sort() because sort comparators with
        // random values don't produce uniform distributions.
        for (let i = candidates.length - 1; i > 0; i--) {
            const j = rng.intRange(0, i);
            const temp = candidates[i];
            candidates[i] = candidates[j];
            candidates[j] = temp;
        }

        // Step 3: Greedily select pads with minimum spacing
        const boostPads = [];
        for (const idx of candidates) {
            if (boostPads.length >= targetCount) break;

            // Check spacing against all already-placed pads.
            // Distance on the circular track considers wrapping.
            let tooClose = false;
            for (const pad of boostPads) {
                let dist = Math.abs(idx - pad.sampleIndex);
                // Account for circular track: the "other way around"
                // might be shorter
                dist = Math.min(dist, totalSamples - dist);
                if (dist < minSpacing) {
                    tooClose = true;
                    break;
                }
            }

            if (!tooClose) {
                boostPads.push({
                    sampleIndex: idx,
                    position: { x: centerPoints[idx].x, y: centerPoints[idx].y },
                    angle: Math.atan2(directions[idx].y, directions[idx].x),
                    width: widths[idx] * 0.6,  // Pad is 60% of track width
                });
            }
        }

        return boostPads;
    }

    // ===================================================================
    // TRACK GENERATION: CENTERLINE VALIDATION
    // ===================================================================

    // Maximum retries when a generated track fails validation.
    // Each retry appends a suffix to the seed so the PRNG produces a
    // different sequence while remaining deterministic.
    const MAX_GENERATION_RETRIES = 3;

    // Minimum segment gap for self-intersection checks. Segments closer
    // than this in the polyline are expected to be near each other and
    // are skipped (adjacent segments share vertices).
    const SELF_INTERSECTION_MIN_GAP = 3;

    /**
     * Checks whether the track centerline crosses itself.
     *
     * A self-intersecting centerline creates a figure-8 shape that
     * breaks lap logic and checkpoint ordering. This performs a sampled
     * check: instead of testing all O(n^2) segment pairs, it tests
     * every Kth segment against all non-adjacent segments ahead of it.
     * This catches global crossings efficiently.
     *
     * @param {Array<{x,y}>} centerPoints - The dense polyline centerline
     * @returns {boolean} True if a self-intersection was found
     */
    function hasCenterlineSelfIntersection(centerPoints) {
        const n = centerPoints.length;

        // Step size: check every 4th segment for crossings. With
        // 320-520 total segments, this gives 80-130 check origins.
        // Each origin tests against segments ahead of it (with gap).
        const step = 4;

        for (let i = 0; i < n; i += step) {
            const iNext = (i + 1) % n;
            const p1 = centerPoints[i];
            const p2 = centerPoints[iNext];

            // Check against segments far enough ahead to not be neighbors
            for (let jRaw = i + SELF_INTERSECTION_MIN_GAP; jRaw < i + n - SELF_INTERSECTION_MIN_GAP; jRaw += step) {
                const j = jRaw % n;
                const jNext = (j + 1) % n;
                const p3 = centerPoints[j];
                const p4 = centerPoints[jNext];

                const hit = segmentIntersection(p1, p2, p3, p4);
                if (hit) return true;
            }
        }

        return false;
    }

    // ===================================================================
    // TRACK GENERATION: BOUNDARY TEST
    // ===================================================================

    /**
     * Tests whether a position is on or off the track.
     *
     * This is called every frame during gameplay for the ship's position.
     * It determines which drag coefficient to apply (on-track = low drag,
     * off-track = high drag) and triggers visual effects (off-track sparks).
     *
     * Algorithm:
     * 1. Find the nearest centerline segment to the test point
     * 2. Project the point onto that segment to find the closest point
     *    on the centerline
     * 3. Calculate the perpendicular distance from the test point to
     *    the centerline
     * 4. Compare that distance to the track's half-width at that point
     *
     * Why nearest-segment and not nearest-point?
     * The centerline is made of discrete sample points. If we only
     * checked distance to the nearest point, there would be blind spots
     * between points. By checking against line SEGMENTS (the lines
     * connecting consecutive points), we get continuous coverage.
     *
     * Performance note: This is O(n) where n is the number of centerline
     * points. For 300 points at 60fps, that's 18,000 distance checks per
     * second. This is fast enough for a single ship. If we later need
     * multiple entities checking simultaneously, we could add a spatial
     * index (grid or quadtree).
     *
     * @param {number} px - Test position X
     * @param {number} py - Test position Y
     * @param {Object} trackData - The full TrackData object
     * @returns {{onTrack: boolean, distFromCenter: number, nearestIndex: number}}
     */
    function isOnTrack(px, py, trackData) {
        const { centerPoints, widths } = trackData;
        const n = centerPoints.length;

        let bestDistSq = Infinity;
        let bestIndex = 0;
        let bestT = 0;  // Interpolation parameter along the best segment

        // Check every segment to find the nearest one.
        // Uses squared distances for comparison to avoid sqrt in the
        // inner loop (~520 iterations per frame). Only one sqrt is
        // needed after the winner is found.
        for (let i = 0; i < n; i++) {
            const next = (i + 1) % n;
            const ax = centerPoints[i].x;
            const ay = centerPoints[i].y;
            const bx = centerPoints[next].x;
            const by = centerPoints[next].y;

            // Project point P onto segment AB.
            // The math: find parameter t where the projection of AP onto AB
            // gives the closest point. t is clamped to [0, 1] to stay
            // within the segment (not the infinite line).
            //
            // t = dot(AP, AB) / dot(AB, AB)
            //   = how far along AB the projection of P falls
            //
            // t=0 means closest to A, t=1 means closest to B,
            // t=0.5 means closest to the midpoint of AB.
            const abx = bx - ax;
            const aby = by - ay;
            const apx = px - ax;
            const apy = py - ay;
            const abLenSq = abx * abx + aby * aby;

            let t;
            if (abLenSq < 0.001) {
                // Degenerate segment (A and B are the same point)
                t = 0;
            } else {
                t = (apx * abx + apy * aby) / abLenSq;
                t = Math.max(0, Math.min(1, t));  // Clamp to segment
            }

            // Closest point on the segment
            const closestX = ax + abx * t;
            const closestY = ay + aby * t;

            // Squared distance from test point to closest point on segment
            const dx = px - closestX;
            const dy = py - closestY;
            const distSq = dx * dx + dy * dy;

            if (distSq < bestDistSq) {
                bestDistSq = distSq;
                bestIndex = i;
                bestT = t;
            }
        }

        // Single sqrt after the loop (instead of one per segment)
        const bestDist = Math.sqrt(bestDistSq);

        // Interpolate the track width at the nearest point.
        // Since we found a point between samples bestIndex and bestIndex+1,
        // we interpolate the width between those two samples for accuracy.
        const nextIndex = (bestIndex + 1) % n;
        const interpolatedWidth = widths[bestIndex] * (1 - bestT) + widths[nextIndex] * bestT;
        const halfWidth = interpolatedWidth / 2;

        return {
            onTrack: bestDist <= halfWidth,
            distFromCenter: bestDist,
            halfWidth: halfWidth,
            nearestIndex: bestIndex,
        };
    }

    // ===================================================================
    // TRACK GENERATION: MAIN GENERATOR
    // ===================================================================

    /**
     * Generates a complete track from a seed string.
     *
     * This is the main entry point for track generation. It orchestrates
     * all the sub-algorithms above into the final TrackData object that
     * every other system consumes.
     *
     * Includes self-intersection validation: if the generated centerline
     * crosses itself (creating a figure-8), the generator retries with a
     * modified seed (up to MAX_GENERATION_RETRIES times). This is rare
     * but possible with aggressive perturbations on 13+ control points.
     *
     * @param {string} seed - The track seed string
     * @param {string} [difficulty='medium'] - Reserved for future archetype system
     * @returns {Object} Complete TrackData object (see GDD Section 3.11)
     */
    function generate(seed, difficulty) {
        for (let attempt = 0; attempt <= MAX_GENERATION_RETRIES; attempt++) {
            const effectiveSeed = attempt === 0 ? seed : seed + '_retry' + attempt;
            const result = generateFromSeed(effectiveSeed, difficulty);

            if (!hasCenterlineSelfIntersection(result.centerPoints)) {
                // Valid track: store the original seed for display purposes
                // (players see "daily-2026-02-16", not "daily-2026-02-16_retry1")
                result.seed = seed;
                return result;
            }

            console.warn(
                `[TrackGenerator] Seed "${effectiveSeed}" produced a self-intersecting track, retrying...`
            );
        }

        // All retries exhausted: use the last attempt anyway. This is
        // extremely unlikely (would need 4 consecutive bad seeds) and
        // the track is still playable, just geometrically odd.
        console.warn(
            `[TrackGenerator] All retries exhausted for seed "${seed}", using last attempt`
        );
        const fallback = generateFromSeed(seed + '_retry' + MAX_GENERATION_RETRIES, difficulty);
        fallback.seed = seed;
        return fallback;
    }

    /**
     * Core track generation logic for a single seed attempt.
     *
     * @param {string} seed - The seed string (may include retry suffix)
     * @param {string} [difficulty='medium'] - Reserved for future archetype system
     * @returns {Object} Complete TrackData object
     */
    function generateFromSeed(seed, difficulty) {
        const rng = new SeededRandom(seed);

        // Step 1: Generate the track skeleton (control points)
        const controlPoints = generateControlPoints(rng);

        // Step 2: Smooth the skeleton into a dense polyline
        const centerPoints = interpolateSpline(controlPoints);
        const totalSamples = centerPoints.length;

        // Step 3: Calculate curvature at each point
        const curvatures = calculateCurvatures(centerPoints);

        // Step 4: Map curvature to track width (with smoothing)
        const widths = calculateWidths(curvatures);

        // Step 5: Generate left/right edge points and tangent directions
        const { leftEdge, rightEdge, directions } = generateEdgePoints(
            centerPoints, widths
        );

        // Step 6: Place checkpoints at even arc-length intervals
        const { checkpoints, totalArcLength } = placeCheckpoints(
            centerPoints, leftEdge, rightEdge, directions, curvatures, rng
        );

        // Step 7: Place boost pads on straight sections
        const boostPads = placeBoostPads(
            centerPoints, curvatures, directions, widths, totalSamples, rng
        );

        // Step 8: Calculate world bounds (bounding box + padding)
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (let i = 0; i < totalSamples; i++) {
            minX = Math.min(minX, leftEdge[i].x, rightEdge[i].x);
            minY = Math.min(minY, leftEdge[i].y, rightEdge[i].y);
            maxX = Math.max(maxX, leftEdge[i].x, rightEdge[i].x);
            maxY = Math.max(maxY, leftEdge[i].y, rightEdge[i].y);
        }

        const bounds = {
            minX: minX - BOUNDS_PADDING,
            minY: minY - BOUNDS_PADDING,
            maxX: maxX + BOUNDS_PADDING,
            maxY: maxY + BOUNDS_PADDING,
        };

        // Step 9: Determine start position and angle from checkpoint 0
        const startCheckpoint = checkpoints[0];
        const startAngle = Math.atan2(
            directions[startCheckpoint.index].y,
            directions[startCheckpoint.index].x
        );

        // Assemble the TrackData object
        return {
            centerPoints,
            leftEdge,
            rightEdge,
            widths,
            curvatures,
            directions,
            checkpoints,
            boostPads,
            startPosition: { x: startCheckpoint.position.x, y: startCheckpoint.position.y },
            startAngle,
            bounds,
            totalArcLength,
            totalSamples,
            seed,
        };
    }

    // ===================================================================
    // TRACK RENDERER
    // ===================================================================
    //
    // Rendering is separated from generation because:
    // 1. Generation is pure math (no Phaser dependency). It can be tested
    //    independently and reused for non-visual purposes (minimap data,
    //    ghost validation).
    // 2. Rendering requires a Phaser scene and creates Phaser objects.
    //    It's inherently visual and tied to the rendering pipeline.
    //
    // The renderer draws the track onto a RenderTexture (a Phaser concept
    // similar to an offscreen canvas). Drawing happens ONCE when the
    // track is generated, then the texture is displayed as a sprite that
    // scrolls with the camera. This is much faster than redrawing 300+
    // line segments every frame.

    /**
     * Tests whether two line segments intersect.
     *
     * Uses the standard cross-product method. Returns the intersection
     * point if the segments cross, or null if they don't.
     *
     * @param {{x,y}} p1 - Start of segment 1
     * @param {{x,y}} p2 - End of segment 1
     * @param {{x,y}} p3 - Start of segment 2
     * @param {{x,y}} p4 - End of segment 2
     * @returns {{x: number, y: number}|null} Intersection point or null
     */
    function segmentIntersection(p1, p2, p3, p4) {
        const d1x = p2.x - p1.x;
        const d1y = p2.y - p1.y;
        const d2x = p4.x - p3.x;
        const d2y = p4.y - p3.y;

        const denom = d1x * d2y - d1y * d2x;

        // Parallel or coincident segments
        if (Math.abs(denom) < 1e-10) return null;

        const dx = p3.x - p1.x;
        const dy = p3.y - p1.y;
        const t = (dx * d2y - dy * d2x) / denom;
        const u = (dx * d1y - dy * d1x) / denom;

        // Both parameters must be strictly within (0, 1) to count as
        // a real crossing. We exclude endpoints (shared vertices between
        // adjacent segments would otherwise always "intersect").
        const eps = 1e-6;
        if (t > eps && t < 1 - eps && u > eps && u < 1 - eps) {
            return {
                x: p1.x + t * d1x,
                y: p1.y + t * d1y,
            };
        }

        return null;
    }

    /**
     * Scans an edge polyline for self-intersections using a windowed check.
     *
     * For each segment i, checks segments j from i+2 to i+LOOP_SCAN_WINDOW
     * for crossing. Returns an array of intersections sorted by segI.
     * Each intersection means the edge crosses itself between segment i
     * and segment j, creating a loop from i+1 to j that should be skipped
     * when rendering.
     *
     * The window size limits the search to local neighborhoods since
     * self-intersection loops are always small (5-20 segments after the
     * geometry fix pass). This keeps the cost at O(n * window) instead
     * of O(n^2).
     *
     * @param {Array<{x,y}>} edge - Edge point array (closed loop)
     * @param {number} n - Number of points
     * @returns {Array<{segI: number, segJ: number, x: number, y: number}>}
     */
    function findEdgeSelfIntersections(edge, n) {
        const intersections = [];

        for (let i = 0; i < n; i++) {
            const iNext = (i + 1) % n;
            const maxJ = Math.min(i + LOOP_SCAN_WINDOW, i + n - 2);

            for (let jRaw = i + 2; jRaw <= maxJ; jRaw++) {
                const j = jRaw % n;
                const jNext = (j + 1) % n;

                const hit = segmentIntersection(
                    edge[i], edge[iNext],
                    edge[j], edge[jNext]
                );

                if (hit) {
                    intersections.push({
                        segI: i,
                        segJ: j,
                        x: hit.x,
                        y: hit.y,
                    });
                }
            }
        }

        // Sort by segI so we can process them in order during rendering
        intersections.sort((a, b) => a.segI - b.segI);
        return intersections;
    }

    /**
     * Strokes an edge polyline, clipping out any self-intersecting loops.
     *
     * If the edge has no self-intersections, this draws the same path as
     * the original code (moveTo edge[0], lineTo through all points, close).
     *
     * If self-intersections are found, the stroke draws up to the first
     * crossing point, jumps (via moveTo) to skip the loop, and continues
     * from the second crossing point onward. Multiple loops are handled
     * by iterating through the sorted intersection list.
     *
     * @param {Phaser.GameObjects.Graphics} g - Graphics object to draw on
     * @param {Array<{x,y}>} edge - Edge point array (closed loop)
     * @param {number} n - Number of points
     * @param {Array} [precomputedIntersections] - Optional cached result from
     *   findEdgeSelfIntersections(). Pass this when stroking the same edge
     *   multiple times (e.g., glow + neon passes) to avoid recomputation.
     */
    function strokeEdgeWithLoopClip(g, edge, n, precomputedIntersections) {
        const intersections = precomputedIntersections || findEdgeSelfIntersections(edge, n);

        if (intersections.length === 0) {
            // No loops: draw the edge normally
            g.beginPath();
            g.moveTo(edge[0].x, edge[0].y);
            for (let i = 1; i < n; i++) {
                g.lineTo(edge[i].x, edge[i].y);
            }
            g.lineTo(edge[0].x, edge[0].y);
            g.strokePath();
            return;
        }

        // Build the path, skipping looped sections.
        // We track which point index we're currently drawing from.
        g.beginPath();
        g.moveTo(edge[0].x, edge[0].y);

        let currentIdx = 1;  // Next point to draw
        let intIdx = 0;      // Next intersection to process

        while (currentIdx < n) {
            // Check if any intersection starts at or before the current segment
            if (intIdx < intersections.length) {
                const ix = intersections[intIdx];

                // The intersection is on segment segI (from edge[segI] to edge[segI+1]).
                // We need to draw up to segI, then draw to the crossing point,
                // then jump past the loop to segJ+1.
                if (currentIdx <= ix.segI + 1) {
                    // Draw points up to and including segI
                    while (currentIdx <= ix.segI) {
                        g.lineTo(edge[currentIdx].x, edge[currentIdx].y);
                        currentIdx++;
                    }
                    // Draw to the crossing point
                    g.lineTo(ix.x, ix.y);

                    // Jump past the loop: move to the crossing point (same
                    // location, but now we continue from segJ+1 onward)
                    const resumeIdx = (ix.segJ + 1) % n;

                    // If resumeIdx wrapped around past 0, the clipped loop
                    // spans the seam. The points from resumeIdx to segI are
                    // the non-clipped portion that still needs drawing.
                    // We draw from resumeIdx to n (end of array), which
                    // reaches back to edge[0] via the closing lineTo below.
                    if (resumeIdx <= ix.segI) {
                        g.moveTo(ix.x, ix.y);
                        for (let k = resumeIdx; k <= ix.segI; k++) {
                            g.lineTo(edge[k].x, edge[k].y);
                        }
                        intIdx++;
                        // No more points to draw: the remaining indices are
                        // inside the clipped section. Fall through to close.
                        break;
                    }

                    g.moveTo(ix.x, ix.y);
                    currentIdx = resumeIdx;
                    intIdx++;
                    continue;
                }
            }

            // No intersection pending: draw the next point normally
            g.lineTo(edge[currentIdx].x, edge[currentIdx].y);
            currentIdx++;
        }

        // Close back to the start
        g.lineTo(edge[0].x, edge[0].y);
        g.strokePath();
    }

    /**
     * Renders a track as a Graphics object in world space.
     *
     * Drawing order (back to front):
     * 1. Track surface (quad-based fill, immune to edge self-intersection)
     * 2. Boost pad arrows
     * 3. Outer glow edges (wide, dim)
     * 4. Inner neon edges (narrow, bright)
     * 5. Checkpoint gates + start/finish line
     *
     * Why direct Graphics instead of RenderTexture?
     * RenderTexture (draw once, display as sprite) is a performance
     * optimization we'll add later. For the prototype, direct Graphics
     * drawing is simpler and easier to debug. The track data doesn't
     * change during a race, so even though the Graphics object exists
     * in the scene, it only gets drawn once by Phaser's renderer
     * (it's not being redrawn per frame like the ship's clear()/redraw).
     * If performance becomes an issue with particles/effects later,
     * we'll switch to RenderTexture then.
     *
     * @param {Phaser.Scene} scene - The active Phaser scene
     * @param {Object} trackData - TrackData from generate()
     * @returns {Phaser.GameObjects.Graphics} The track graphics object
     */
    function renderTrack(scene, trackData) {
        const {
            leftEdge, rightEdge,
            checkpoints, boostPads, totalSamples,
        } = trackData;

        // Draw directly in world coordinates. No offset needed because
        // the track geometry is already in world space (centered around 0,0).
        const g = scene.add.graphics();

        // ----- Layer 1: Track Surface (quad-based fill) -----
        // Each consecutive pair of centerline samples forms a trapezoid
        // quad between the left and right edge points. On tight corners
        // the inside edge self-intersects and quads overlap, but at full
        // opacity the overlap is invisible (opaque on opaque = no change).
        g.fillStyle(0x141428, 1.0);
        for (let i = 0; i < totalSamples; i++) {
            const next = (i + 1) % totalSamples;
            g.beginPath();
            g.moveTo(leftEdge[i].x, leftEdge[i].y);
            g.lineTo(leftEdge[next].x, leftEdge[next].y);
            g.lineTo(rightEdge[next].x, rightEdge[next].y);
            g.lineTo(rightEdge[i].x, rightEdge[i].y);
            g.closePath();
            g.fillPath();
        }

        // ----- Layer 2: Boost Pad Arrows -----
        // Small directional arrows showing where boost pads are and
        // which direction they push.
        for (const pad of boostPads) {
            const px = pad.position.x;
            const py = pad.position.y;
            const angle = pad.angle;
            const padLen = 30;
            const padWidth = 8;

            // Arrow body
            g.fillStyle(0xf77622, 0.5);
            g.beginPath();

            // Arrow tip
            const tipX = px + Math.cos(angle) * padLen;
            const tipY = py + Math.sin(angle) * padLen;

            // Arrow base corners (perpendicular to direction)
            const perpX = -Math.sin(angle);
            const perpY = Math.cos(angle);
            const baseX = px - Math.cos(angle) * padLen * 0.5;
            const baseY = py - Math.sin(angle) * padLen * 0.5;

            g.moveTo(tipX, tipY);
            g.lineTo(baseX + perpX * padWidth, baseY + perpY * padWidth);
            g.lineTo(baseX - perpX * padWidth, baseY - perpY * padWidth);
            g.closePath();
            g.fillPath();

            // Bright center line
            g.lineStyle(2, 0xf77622, 0.8);
            g.beginPath();
            g.moveTo(baseX, baseY);
            g.lineTo(tipX, tipY);
            g.strokePath();
        }

        // ----- Pre-compute edge self-intersections -----
        // Both the glow and neon passes stroke the same edges, so we
        // compute intersections once per edge instead of 4x total.
        const leftIntersections = findEdgeSelfIntersections(leftEdge, totalSamples);
        const rightIntersections = findEdgeSelfIntersections(rightEdge, totalSamples);

        // ----- Layer 3: Outer Glow Edges -----
        // Wide, dim lines that create the "glow" effect around the track.
        // First pass of the two-pass neon technique. Uses loop clipping
        // to skip any self-intersecting sections on tight corners.
        g.lineStyle(6, 0x0a8ea0, 0.4);
        strokeEdgeWithLoopClip(g, leftEdge, totalSamples, leftIntersections);
        strokeEdgeWithLoopClip(g, rightEdge, totalSamples, rightIntersections);

        // ----- Layer 4: Inner Neon Edges -----
        // Narrow, bright lines over the glow. The "core" of the neon
        // effect: bright center with dim spread around it.
        g.lineStyle(2, 0x2ce8f5, 1.0);
        strokeEdgeWithLoopClip(g, leftEdge, totalSamples, leftIntersections);
        strokeEdgeWithLoopClip(g, rightEdge, totalSamples, rightIntersections);

        // ----- Layer 5: Checkpoint Gates -----
        // Lines drawn across the track at each checkpoint position.
        // Checkpoint 0 (start/finish) gets special treatment.
        for (let c = 0; c < checkpoints.length; c++) {
            const cp = checkpoints[c];
            const lx = cp.leftPoint.x;
            const ly = cp.leftPoint.y;
            const rx = cp.rightPoint.x;
            const ry = cp.rightPoint.y;

            if (c === 0) {
                // Start/finish line: gold, thicker
                g.lineStyle(4, 0xd4a017, 0.9);
                g.beginPath();
                g.moveTo(lx, ly);
                g.lineTo(rx, ry);
                g.strokePath();

                // Dashed center line effect (3 short segments)
                g.lineStyle(2, 0xffffff, 0.6);
                const dx = rx - lx;
                const dy = ry - ly;
                for (let d = 0.2; d < 0.9; d += 0.3) {
                    g.beginPath();
                    g.moveTo(lx + dx * d, ly + dy * d);
                    g.lineTo(lx + dx * (d + 0.1), ly + dy * (d + 0.1));
                    g.strokePath();
                }
            } else {
                // Regular checkpoint: green, thinner
                g.lineStyle(2, 0x40e850, 0.5);
                g.beginPath();
                g.moveTo(lx, ly);
                g.lineTo(rx, ry);
                g.strokePath();
            }
        }

        return g;
    }

    /**
     * Renders a simplified minimap of the track.
     *
     * The minimap is a small representation of the track for the HUD.
     * It only draws the centerline (no surface fill, no glow), scaled
     * to fit within the specified dimensions.
     *
     * @param {Phaser.Scene} scene - The active Phaser scene
     * @param {Object} trackData - TrackData from generate()
     * @param {number} x - Minimap screen X position
     * @param {number} y - Minimap screen Y position
     * @param {number} w - Minimap width in pixels
     * @param {number} h - Minimap height in pixels
     * @returns {{graphics: Phaser.GameObjects.Graphics, mapData: Object}}
     *   graphics: The minimap Graphics object to add to the scene.
     *   mapData: Coordinate mapping info for positioning the player dot.
     */
    function renderMinimap(scene, trackData, x, y, w, h) {
        const { centerPoints, bounds, checkpoints } = trackData;

        // Calculate scale to fit the track within the minimap dimensions.
        // We use the smaller of width/height scales to maintain aspect ratio
        // (the track won't be stretched).
        const trackWidth = bounds.maxX - bounds.minX;
        const trackHeight = bounds.maxY - bounds.minY;
        const scaleX = w / trackWidth;
        const scaleY = h / trackHeight;
        const mapScale = Math.min(scaleX, scaleY) * 0.9; // 90% to add margin

        // Center offset within the minimap area
        const centerX = x + w / 2;
        const centerY = y + h / 2;
        const trackCenterX = (bounds.minX + bounds.maxX) / 2;
        const trackCenterY = (bounds.minY + bounds.maxY) / 2;

        const g = scene.add.graphics();

        // Background
        g.fillStyle(0x0a0a14, 0.7);
        g.fillRect(x, y, w, h);
        g.lineStyle(1, 0x2ce8f5, 0.3);
        g.strokeRect(x, y, w, h);

        // Track centerline
        g.lineStyle(1.5, 0x2ce8f5, 0.4);
        g.beginPath();

        for (let i = 0; i < centerPoints.length; i++) {
            const mx = centerX + (centerPoints[i].x - trackCenterX) * mapScale;
            const my = centerY + (centerPoints[i].y - trackCenterY) * mapScale;

            if (i === 0) {
                g.moveTo(mx, my);
            } else {
                g.lineTo(mx, my);
            }
        }
        // Close the loop
        const mx0 = centerX + (centerPoints[0].x - trackCenterX) * mapScale;
        const my0 = centerY + (centerPoints[0].y - trackCenterY) * mapScale;
        g.lineTo(mx0, my0);
        g.strokePath();

        // Start/finish marker (small gold dot)
        const startCP = checkpoints[0];
        const startMX = centerX + (startCP.position.x - trackCenterX) * mapScale;
        const startMY = centerY + (startCP.position.y - trackCenterY) * mapScale;
        g.fillStyle(0xd4a017, 1.0);
        g.fillCircle(startMX, startMY, 3);

        // Return both the graphics object and the coordinate mapping data
        // separately. Previously _mapData was monkey-patched onto the
        // Graphics object, which is fragile if Phaser ever uses that name.
        const mapData = {
            centerX, centerY, mapScale,
            trackCenterX, trackCenterY,
        };

        return { graphics: g, mapData };
    }

    // ===================================================================
    // PUBLIC API
    // ===================================================================

    PlatPursuit.Games.Driver.TrackGenerator = {
        /** The SeededRandom class (exposed for testing/debugging) */
        SeededRandom,

        /** Generate a complete track from a seed string */
        generate,

        /** Test if a point is on the track */
        isOnTrack,

        /** Render track to a RenderTexture sprite */
        renderTrack,

        /** Render a minimap of the track */
        renderMinimap,
    };

    console.log('[Stellar Circuit] TrackGenerator loaded');
})();
