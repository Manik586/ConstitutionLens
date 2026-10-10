// Subtle GSAP motion. Purely decorative: every function is a no-op when GSAP did not load
// or the user prefers reduced motion, and the UI never waits on an animation to be usable.
// GSAP writes styles through the CSSOM, which the page's CSP (style-src 'self') permits.

const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const G = !reduce && window.gsap ? window.gsap : null;

export const motionEnabled = Boolean(G);

export function intro() {
  if (!G) return;
  G.timeline({ defaults: { ease: "power3.out" } })
    .from(".brand__mark", { rotation: -90, scale: .6, opacity: 0, duration: .8, ease: "back.out(1.8)" })
    .from(".brand__name, .brand__sub", { x: -10, opacity: 0, stagger: .08, duration: .6 }, "<.15")
    .from(".side-head", { opacity: 0, y: 6, duration: .5 }, "<.2")
    .from(".corpus-head, #askSection", { opacity: 0, y: 14, stagger: .1, duration: .7 }, "<")
    .from(".banner", { opacity: 0, duration: .6 }, "<.3");
}

export function listIn(items) {
  if (!G || !items.length) return;
  G.from(items, { opacity: 0, x: -8, stagger: .04, duration: .45, ease: "power2.out", clearProps: "opacity,transform" });
}

export function swapIn(nodes) {
  if (!G) return;
  G.fromTo(nodes, { opacity: 0, y: 8 }, { opacity: 1, y: 0, stagger: .06, duration: .45, ease: "power2.out", clearProps: "opacity,transform" });
}

export function pulse(node) {
  if (!G || !node) return;
  G.fromTo(node, { scale: 1.12 }, { scale: 1, duration: .5, ease: "back.out(3)", clearProps: "transform" });
}

export function revealPanels(panels) {
  if (!G) return;
  G.fromTo(panels, { opacity: 0, y: 18 }, { opacity: 1, y: 0, stagger: .12, duration: .6, ease: "power3.out", clearProps: "opacity,transform" });
  const claims = panels[0] ? panels[0].querySelectorAll(".claim") : [];
  if (claims.length) G.from(claims, { opacity: 0, y: 8, stagger: .06, duration: .45, delay: .15, ease: "power2.out", clearProps: "opacity,transform" });
}

export function flash(node) {
  if (!G || !node) return;
  G.fromTo(node, { boxShadow: "0 0 0 6px rgba(122, 31, 43, .28)" }, { boxShadow: "0 0 0 0 rgba(122, 31, 43, 0)", duration: 1.1, ease: "power2.out", clearProps: "boxShadow" });
}

export function dialogIn(dialog) {
  if (!G) return;
  G.fromTo(dialog, { opacity: 0, scale: .96, y: 8 }, { opacity: 1, scale: 1, y: 0, duration: .3, ease: "power2.out", clearProps: "opacity,transform" });
}

export function expand(node) {
  if (!G || !node) return;
  G.fromTo(node, { opacity: 0, height: 0 }, { opacity: 1, height: "auto", duration: .35, ease: "power2.out", clearProps: "opacity,height" });
}
