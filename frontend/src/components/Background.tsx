/** The page ground: near-black, with a soft white spotlight from the top and a faint dot
 *  grid that fades out below it — depth without colour. Static CSS: no WebGL, no animation
 *  (the old animated "Nighty Night" gradient belonged to the violet theme). */
export function Background() {
  return (
    <>
      <div aria-hidden style={{ position: "fixed", inset: 0, zIndex: -3, background: "var(--bg)" }} />
      <div
        aria-hidden
        style={{
          position: "fixed",
          inset: 0,
          zIndex: -2,
          pointerEvents: "none",
          background: "radial-gradient(900px 520px at 50% -140px, rgba(255,255,255,.085), transparent 70%)",
        }}
      />
      <div
        aria-hidden
        style={{
          position: "fixed",
          inset: 0,
          zIndex: -1,
          pointerEvents: "none",
          backgroundImage: "radial-gradient(rgba(255,255,255,.07) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
          maskImage: "radial-gradient(1000px 560px at 50% 0, #000 10%, transparent 75%)",
          WebkitMaskImage: "radial-gradient(1000px 560px at 50% 0, #000 10%, transparent 75%)",
        }}
      />
    </>
  );
}
