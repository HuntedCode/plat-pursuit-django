/**
 * ESLint, for the class of bug the test suite structurally cannot see.
 *
 * WHY THIS EXISTS. `static/js/game-flag.js` shipped with `maxVersions` used in four places and
 * declared in none. That is valid JavaScript: `node --check` passed, `npm run build` passed,
 * `collectstatic` passed, and every Django test stayed green because the MARKUP contract was correct
 * -- the bug was purely in the script. It reached a browser and silently killed a button, because a
 * ReferenceError in a click handler aborts the handler and logs to a console nobody is watching.
 *
 * There is no JS test harness in this repo and this is not an argument for one. It is the cheap half:
 * a whole-file static pass that answers "does every name resolve" without anybody writing a test.
 *
 * SCOPE. `static/js` only, minus `vendor/` (third-party bundles we do not edit, minified past the
 * point of useful analysis). `static/js/games/` IS included -- it is ours.
 *
 * GLOBALS come from the `globals` package rather than a hand-written list. The hand-written version
 * was tried first and immediately wanted `confirm`, `alert`, `KeyboardEvent`, `performance`,
 * `cancelAnimationFrame`, `Option`, `CSS`, `prompt`, `FileReader`, `DeviceOrientationEvent`, `Node`
 * and `HTMLTextAreaElement` -- a list that is never finished, and whose every omission is a false
 * positive somebody has to silence. Only the things the browser does NOT provide are named below:
 * our own namespace, and the four libraries loaded from CDNs.
 */
import js from '@eslint/js';
import globals from 'globals';

export default [
  {
    ignores: [
      'static/js/vendor/**',
      'staticfiles/**',
      'node_modules/**',
      'venv/**',
    ],
  },
  js.configs.recommended,
  {
    files: ['static/js/**/*.js'],
    languageOptions: {
      ecmaVersion: 2022,
      // `script`, not `module`: these are plain <script src> files sharing one global scope, which
      // is what the IIFE-and-namespace pattern throughout `static/js` assumes.
      sourceType: 'script',
      globals: {
        ...globals.browser,
        // Ours: the namespace every module hangs off (static/js/utils.js).
        PlatPursuit: 'readonly',
        // Loaded from CDNs or vendor/, so present at runtime and invisible to static analysis.
        htmx: 'readonly',
        Sortable: 'readonly',
        Phaser: 'readonly',
        confetti: 'readonly',
        marked: 'readonly',
        DOMPurify: 'readonly',
      },
    },
    rules: {
      // THE rule this gate exists for. Everything else recommended brings is a bonus that happened
      // to already pass -- see the note in package.json's `lint` script.
      'no-undef': 'error',
      // WARN, NOT ERROR, and the distinction is the whole shape of this gate: eslint exits 0 on
      // warnings, so CI stays green while `npm run lint` still shows them.
      //
      // There are 10, all pre-existing dead assignments, in roadmap_editor, roadmap_notes,
      // review-hub, platinum-grid-wizard, recently-added and fundraiser. Deleting dead code is the
      // house rule and they should go -- but they are six unrelated features, and rewriting them
      // from a branch about colour mixes is the scope creep the same rules forbid. Promote to
      // 'error' with the cleanup; until then the list is one command away instead of invisible.
      //
      // `args: 'none'` stays regardless: unused parameters are how a callback declares a signature
      // it does not need all of. `caughtErrors: 'none'` for the same reason one level down.
      'no-unused-vars': ['warn', { args: 'none', caughtErrors: 'none' }],
      // Same treatment, 3 instances (roadmap_editor, roadmap_notes). A useless escape is a no-op by
      // definition, so these are safe to delete -- but they are regex edits in files this branch has
      // no other reason to open, and regex is where a "safe" edit has bitten hardest here.
      'no-useless-escape': 'warn',
      // `allowEmptyCatch`, ACCOMMODATION NOT ENDORSEMENT. 27 of the 40 problems the recommended set
      // first reported were `catch (e) {}` -- overwhelmingly localStorage and JSON guards where the
      // failure genuinely is "carry on". The owner's standing guidance is that a bare catch is a
      // smell, and it is; but turning that into 27 edits across a dozen unrelated files would make a
      // lint gate the most invasive change in a branch about colour mixes, and a gate that lands red
      // is a gate somebody disables.
      //
      // Flip this to `false` to get the list back whenever that cleanup is worth doing. That is the
      // point of having the gate: the work is now findable in one command.
      'no-empty': ['error', { allowEmptyCatch: true }],
    },
  },
];
