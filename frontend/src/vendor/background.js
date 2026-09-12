/* clipfinder animated background — standalone WebGL2.
 *
 * A faithful, dependency-free port of shadergradient's "Nighty Night" preset
 * (github.com/ruucm/shadergradient, MIT). Reproduces its exact noise + gradient
 * math on a displaced plane; the three.js PBR/lighting stack (ambient-only in
 * this preset) is replaced with a plain ACES tone-map. ~8 KB, no framework.
 *
 * Public API:  mountBackground(el) -> teardown()
 */

const PRESET = {
  color1: [0x60 / 255, 0x60 / 255, 0x80 / 255], // #606080
  color2: [0x8d / 255, 0x7d / 255, 0xca / 255], // #8d7dca
  color3: [0x21 / 255, 0x21 / 255, 0x21 / 255], // #212121
  uSpeed: 0.3,
  uDensity: 1.5,
  uStrength: 1.5,
  brightness: 0.9,
  // camera-controls: polar from +Y, azimuth, distance
  cPolarAngle: 80,
  cAzimuthAngle: 180,
  cDistance: 2.8,
  fov: 45,
  // mesh transform (three.js Euler 'XYZ', degrees)
  rotation: [50, 0, -60],
  segments: 160, // 192² in the original; 160² is visually identical, cheaper
};

const VERT = `#version 300 es
precision highp float;

// classic 3D Perlin noise — lifted verbatim from shadergradient waterPlane/vertex.glsl
vec3 mod289(vec3 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
vec4 mod289(vec4 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
vec4 permute(vec4 x){ return mod289(((x*34.0)+1.0)*x); }
vec4 taylorInvSqrt(vec4 r){ return 1.79284291400159 - 0.85373472095314 * r; }
vec3 fade(vec3 t){ return t*t*t*(t*(t*6.0-15.0)+10.0); }
float cnoise(vec3 P){
  vec3 Pi0 = floor(P), Pi1 = Pi0 + vec3(1.0);
  Pi0 = mod289(Pi0); Pi1 = mod289(Pi1);
  vec3 Pf0 = fract(P), Pf1 = Pf0 - vec3(1.0);
  vec4 ix = vec4(Pi0.x, Pi1.x, Pi0.x, Pi1.x);
  vec4 iy = vec4(Pi0.yy, Pi1.yy);
  vec4 iz0 = Pi0.zzzz, iz1 = Pi1.zzzz;
  vec4 ixy = permute(permute(ix) + iy);
  vec4 ixy0 = permute(ixy + iz0);
  vec4 ixy1 = permute(ixy + iz1);
  vec4 gx0 = ixy0 * (1.0/7.0);
  vec4 gy0 = fract(floor(gx0) * (1.0/7.0)) - 0.5;
  gx0 = fract(gx0);
  vec4 gz0 = vec4(0.5) - abs(gx0) - abs(gy0);
  vec4 sz0 = step(gz0, vec4(0.0));
  gx0 -= sz0 * (step(0.0, gx0) - 0.5);
  gy0 -= sz0 * (step(0.0, gy0) - 0.5);
  vec4 gx1 = ixy1 * (1.0/7.0);
  vec4 gy1 = fract(floor(gx1) * (1.0/7.0)) - 0.5;
  gx1 = fract(gx1);
  vec4 gz1 = vec4(0.5) - abs(gx1) - abs(gy1);
  vec4 sz1 = step(gz1, vec4(0.0));
  gx1 -= sz1 * (step(0.0, gx1) - 0.5);
  gy1 -= sz1 * (step(0.0, gy1) - 0.5);
  vec3 g000 = vec3(gx0.x,gy0.x,gz0.x), g100 = vec3(gx0.y,gy0.y,gz0.y);
  vec3 g010 = vec3(gx0.z,gy0.z,gz0.z), g110 = vec3(gx0.w,gy0.w,gz0.w);
  vec3 g001 = vec3(gx1.x,gy1.x,gz1.x), g101 = vec3(gx1.y,gy1.y,gz1.y);
  vec3 g011 = vec3(gx1.z,gy1.z,gz1.z), g111 = vec3(gx1.w,gy1.w,gz1.w);
  vec4 norm0 = taylorInvSqrt(vec4(dot(g000,g000), dot(g010,g010), dot(g100,g100), dot(g110,g110)));
  g000 *= norm0.x; g010 *= norm0.y; g100 *= norm0.z; g110 *= norm0.w;
  vec4 norm1 = taylorInvSqrt(vec4(dot(g001,g001), dot(g011,g011), dot(g101,g101), dot(g111,g111)));
  g001 *= norm1.x; g011 *= norm1.y; g101 *= norm1.z; g111 *= norm1.w;
  float n000 = dot(g000, Pf0);
  float n100 = dot(g100, vec3(Pf1.x, Pf0.yz));
  float n010 = dot(g010, vec3(Pf0.x, Pf1.y, Pf0.z));
  float n110 = dot(g110, vec3(Pf1.xy, Pf0.z));
  float n001 = dot(g001, vec3(Pf0.xy, Pf1.z));
  float n101 = dot(g101, vec3(Pf1.x, Pf0.y, Pf1.z));
  float n011 = dot(g011, vec3(Pf0.x, Pf1.yz));
  float n111 = dot(g111, Pf1);
  vec3 fade_xyz = fade(Pf0);
  vec4 n_z = mix(vec4(n000,n100,n010,n110), vec4(n001,n101,n011,n111), fade_xyz.z);
  vec2 n_yz = mix(n_z.xy, n_z.zw, fade_xyz.y);
  return 2.2 * mix(n_yz.x, n_yz.y, fade_xyz.x);
}

uniform mat4 uProj, uView, uModel;
uniform float uTime, uSpeed, uDensity, uStrength;
in vec2 aPos;              // plane vertex in [-5,5]^2
out vec3 vPos;             // displaced local position (matches shadergradient vPos)

float displace(vec2 p){
  float t = uTime * uSpeed;
  vec3 np = 0.43 * vec3(p, 0.0) * uDensity;
  return 0.75 * cnoise(np + t) * uStrength;
}

void main(){
  float d = displace(aPos);
  vec3 pos = vec3(aPos, d);
  vPos = pos;
  gl_Position = uProj * uView * uModel * vec4(pos, 1.0);
}`;

const FRAG = `#version 300 es
precision highp float;
uniform vec3 uC1, uC2, uC3;
uniform float uBrightness;
in vec3 vPos;
out vec4 frag;

// ACES filmic approximation (matches three.js flat:true tone mapping closely)
vec3 aces(vec3 x){
  return clamp((x*(2.51*x+0.03))/(x*(2.43*x+0.59)+0.14), 0.0, 1.0);
}

void main(){
  // shadergradient waterPlane/fragment.glsl gradient, verbatim
  vec3 grad = mix(mix(uC1, uC2, smoothstep(-3.0, 3.0, vPos.x)), uC3, vPos.z);
  frag = vec4(aces(grad * uBrightness), 1.0);
}`;

/* ---------- tiny mat4 helpers (column-major) ---------- */
function mIdent() { return [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]; }
function mMul(a, b) {
  const o = new Array(16);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    o[c * 4 + r] = a[r] * b[c*4] + a[4+r] * b[c*4+1] + a[8+r] * b[c*4+2] + a[12+r] * b[c*4+3];
  }
  return o;
}
function mPerspective(fovDeg, aspect, near, far) {
  const f = 1 / Math.tan((fovDeg * Math.PI / 180) / 2);
  const nf = 1 / (near - far);
  return [
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ];
}
function mLookAt(eye, target, up) {
  const z = norm3(sub3(eye, target));
  const x = norm3(cross3(up, z));
  const y = cross3(z, x);
  return [
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -dot3(x, eye), -dot3(y, eye), -dot3(z, eye), 1,
  ];
}
function mRotX(a){ const c=Math.cos(a), s=Math.sin(a); return [1,0,0,0, 0,c,s,0, 0,-s,c,0, 0,0,0,1]; }
function mRotY(a){ const c=Math.cos(a), s=Math.sin(a); return [c,0,-s,0, 0,1,0,0, s,0,c,0, 0,0,0,1]; }
function mRotZ(a){ const c=Math.cos(a), s=Math.sin(a); return [c,s,0,0, -s,c,0,0, 0,0,1,0, 0,0,0,1]; }
const sub3 = (a,b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const dot3 = (a,b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
const cross3 = (a,b) => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
const norm3 = (a) => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0]/l, a[1]/l, a[2]/l]; };
const rad = (d) => d * Math.PI / 180;

function compile(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(s) || "shader compile failed");
  }
  return s;
}

function buildPlane(seg) {
  // PlaneGeometry(10,10,seg,seg) — XY plane, x,y in [-5,5]
  const verts = [];
  const idx = [];
  for (let j = 0; j <= seg; j++) {
    for (let i = 0; i <= seg; i++) {
      verts.push((i / seg) * 10 - 5, (j / seg) * 10 - 5);
    }
  }
  const row = seg + 1;
  for (let j = 0; j < seg; j++) {
    for (let i = 0; i < seg; i++) {
      const a = j * row + i, b = a + 1, c = a + row, d = c + 1;
      idx.push(a, c, b, b, c, d);
    }
  }
  return { verts: new Float32Array(verts), idx: new Uint32Array(idx) };
}

export function mountBackground(el) {
  if (!el) return () => {};
  const canvas = document.createElement("canvas");
  canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block";
  el.appendChild(canvas);

  const gl = canvas.getContext("webgl2", { antialias: true, alpha: false, powerPreference: "low-power" });
  if (!gl) { return () => canvas.remove(); } // leaves the CSS fallback showing

  const prog = gl.createProgram();
  gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
  gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn("background: link failed", gl.getProgramInfoLog(prog));
    return () => canvas.remove();
  }
  gl.useProgram(prog);

  const { verts, idx } = buildPlane(PRESET.segments);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const vbo = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, vbo);
  gl.bufferData(gl.ARRAY_BUFFER, verts, gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "aPos");
  gl.enableVertexAttribArray(loc);
  gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const ibo = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibo);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, idx, gl.STATIC_DRAW);

  const U = (n) => gl.getUniformLocation(prog, n);
  const uProj = U("uProj"), uView = U("uView"), uModel = U("uModel");
  const uTime = U("uTime"), uSpeed = U("uSpeed"), uDensity = U("uDensity"), uStrength = U("uStrength");
  gl.uniform1f(uSpeed, PRESET.uSpeed);
  gl.uniform1f(uDensity, PRESET.uDensity);
  gl.uniform1f(uStrength, PRESET.uStrength);
  gl.uniform1f(U("uBrightness"), PRESET.brightness);
  gl.uniform3fv(U("uC1"), PRESET.color1);
  gl.uniform3fv(U("uC2"), PRESET.color2);
  gl.uniform3fv(U("uC3"), PRESET.color3);

  // model: three.js Euler order 'XYZ' — combined matrix is Rx · Ry · Rz (Rz hits the vector first)
  const [rx, ry, rz] = PRESET.rotation.map(rad);
  const model = mMul(mRotX(rx), mMul(mRotY(ry), mRotZ(rz)));
  gl.uniformMatrix4fv(uModel, false, model);

  // camera-controls spherical: polar from +Y, azimuth around Y
  const th = rad(PRESET.cPolarAngle), ph = rad(PRESET.cAzimuthAngle), r = PRESET.cDistance;
  const eye = [r * Math.sin(th) * Math.sin(ph), r * Math.cos(th), r * Math.sin(th) * Math.cos(ph)];
  const view = mLookAt(eye, [0, 0, 0], [0, 1, 0]);
  gl.uniformMatrix4fv(uView, false, view);

  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let raf = 0, t0 = performance.now(), paused = false;

  function resize() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(el.clientWidth * dpr));
    const h = Math.max(1, Math.round(el.clientHeight * dpr));
    if (canvas.width === w && canvas.height === h) return;
    canvas.width = w; canvas.height = h;
    gl.viewport(0, 0, w, h);
    gl.uniformMatrix4fv(uProj, false, mPerspective(PRESET.fov, w / h, 0.1, 100));
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    if (paused) return;
    resize();
    gl.uniform1f(uTime, reduce ? 8 : (now - t0) / 1000 + 8);
    gl.clearColor(0.06, 0.055, 0.09, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.drawElements(gl.TRIANGLES, idx.length, gl.UNSIGNED_INT, 0);
    if (reduce) { cancelAnimationFrame(raf); raf = 0; } // one frame, then stop
  }
  raf = requestAnimationFrame(frame);

  const onVis = () => {
    paused = document.hidden;
    if (!paused && !raf && !reduce) { t0 = performance.now() - 8000; raf = requestAnimationFrame(frame); }
  };
  document.addEventListener("visibilitychange", onVis);
  const ro = new ResizeObserver(resize);
  ro.observe(el);

  return () => {
    if (raf) cancelAnimationFrame(raf);
    document.removeEventListener("visibilitychange", onVis);
    ro.disconnect();
    canvas.remove();
  };
}
