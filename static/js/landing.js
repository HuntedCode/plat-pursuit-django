/**
 * Landing Page Animation Controller
 *
 * Three lightweight animation systems for the marketing landing page:
 * 1. Scroll Reveal: IntersectionObserver-based element reveals
 * 2. Count-Up: Animated number counters for stats
 * 3. Parallax: Subtle vertical offset on scroll (desktop only)
 *
 * All animations respect prefers-reduced-motion.
 */
(function () {
  'use strict';

  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ─── Scroll Reveal ──────────────────────────────────────────────────
  function initScrollReveal() {
    const elements = document.querySelectorAll('.reveal, .reveal-left, .reveal-right');
    if (!elements.length) return;

    if (prefersReducedMotion) {
      elements.forEach(el => el.classList.add('revealed'));
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('revealed');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -50px 0px' }
    );

    elements.forEach(el => observer.observe(el));
  }

  // ─── Count-Up Animator ──────────────────────────────────────────────
  function easeOutExpo(t) {
    return t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
  }

  function animateCount(el) {
    const target = parseInt(el.dataset.countTarget, 10);
    if (isNaN(target)) return;

    const duration = 2000;
    const formatter = new Intl.NumberFormat();
    const start = performance.now();

    function step(now) {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      const value = Math.floor(easeOutExpo(progress) * target);
      el.textContent = formatter.format(value);
      if (progress < 1) requestAnimationFrame(step);
    }

    requestAnimationFrame(step);
  }

  function initCountUp() {
    const counters = document.querySelectorAll('[data-count-target]');
    if (!counters.length) return;

    if (prefersReducedMotion) {
      const formatter = new Intl.NumberFormat();
      counters.forEach(el => {
        const target = parseInt(el.dataset.countTarget, 10);
        if (!isNaN(target)) el.textContent = formatter.format(target);
      });
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            animateCount(entry.target);
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.3 }
    );

    counters.forEach(el => observer.observe(el));
  }

  // ─── Lightweight Parallax ───────────────────────────────────────────
  function initParallax() {
    if (prefersReducedMotion) return;
    if (window.innerWidth < 768) return;

    const elements = document.querySelectorAll('[data-parallax]');
    if (!elements.length) return;

    let ticking = false;

    window.addEventListener(
      'scroll',
      () => {
        if (!ticking) {
          requestAnimationFrame(() => {
            elements.forEach((el) => {
              const rect = el.getBoundingClientRect();
              if (rect.bottom > -200 && rect.top < window.innerHeight + 200) {
                const center = rect.top + rect.height / 2;
                const viewCenter = window.innerHeight / 2;
                const offset = (center - viewCenter) * 0.04;
                const clamped = Math.max(-20, Math.min(20, offset));
                el.style.transform = `translateY(${clamped}px)`;
              }
            });
            ticking = false;
          });
          ticking = true;
        }
      },
      { passive: true }
    );
  }

  // ─── Initialize ─────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    initScrollReveal();
    initCountUp();
    initParallax();
  });
})();
