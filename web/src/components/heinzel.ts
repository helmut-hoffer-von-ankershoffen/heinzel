// Heinzelmännchen's picture, an original drawing in a flat, friendly emoji style: a round yellow face with big shiny
// eyes, rosy cheeks and a smile, rounded ears, a scalloped fringe, a shirt with a belt, trousers and boots. A person
// styles each Heinzelmännchen with five choices: its age (which also sets the cap: none for a baby, then small, medium
// and tall), its beard, its hat's colour, its shirt's colour and its build. Its state shows in its face, its posture
// and a small badge (asleep, working, thinking, waiting for you, blocked, done).
//
// The drawing is a full figure on a 512 by 860 canvas; its top 512 by 512 square is the head-and-shoulders crop that
// small slots and the icons show. `make icons` writes heinzel.svg (the default one, cropped: the favicon and the small
// icons) and heinzel-swarm.svg (a busy group, the product's logo) from here; everything else is made from those.
// Plain TypeScript with no imports, so node runs this file as it is.

/** A palette whose every colour is at least 3:1 against both a light and a dark background. */
export const SHIRTS = {
  green: "#43A047",
  blue: "#1E88E5",
  purple: "#8E5BD8",
  "red-brown": "#B5532F",
  ochre: "#B7841C",
  teal: "#1F9488",
  slate: "#64748B",
  rose: "#D9567C",
} as const;
export const HATS = {
  red: "#E8572A",
  rust: "#B5532F",
  ochre: "#B7841C",
  green: "#43A047",
  teal: "#1F9488",
  blue: "#1E88E5",
  purple: "#8E5BD8",
  slate: "#64748B",
} as const;
export type Shirt = keyof typeof SHIRTS;
export type Hat = keyof typeof HATS;

export const AGES = ["baby", "young", "grown", "old"] as const;
export type Age = (typeof AGES)[number];
export const BEARDS = ["none", "short", "medium", "long"] as const;
export type Beard = (typeof BEARDS)[number];
export const BUILDS = ["slim", "regular", "round"] as const;
export type Build = (typeof BUILDS)[number];
export const STATES = ["idle", "sleeping", "working", "thinking", "waiting", "blocked", "done"] as const;
export type HeinzelState = (typeof STATES)[number];
export const VIEWS = ["full", "bust"] as const;
export type View = (typeof VIEWS)[number];

/** The bust's square (x, y, side) on the canvas: the head, its cap and the shoulders, tight for each age's cap. */
const BUST: Record<Age, [number, number, number]> = {
  baby: [95, 190, 322],
  young: [80, 160, 352],
  grown: [47.5, 95, 417],
  old: [0, 0, 512],
};

/** The viewBox of a Heinzelmännchen of this age: the whole figure, or its bust. */
export function viewBox(age: Age = "grown", view: View = "full"): string {
  if (view === "full") return "0 0 512 860";
  const [x, y, side] = BUST[age];
  return `${x} ${y} ${side} ${side}`;
}

/** How a Heinzelmännchen looks. A baby has no beard, whatever is asked. */
export type HeinzelLook = { age?: Age; beard?: Beard; hat?: Hat; shirt?: Shirt; build?: Build; state?: HeinzelState };

type AgeLook = { tip: number | null; hair: string; brow: string; light: string; eye: number; beard: Beard };

/** What age sets: the cap's tip (none for a baby), hair and beard colour, the eyes' size and the beard it starts with. */
const BY_AGE: Record<Age, AgeLook> = {
  baby: { tip: null, hair: "#EDB22F", brow: "#B9801C", light: "#F6C95A", eye: 26, beard: "none" },
  young: { tip: 10, hair: "#E3A12B", brow: "#A86F17", light: "#F0BE55", eye: 24, beard: "none" },
  grown: { tip: -90, hair: "#B5651D", brow: "#7A3F12", light: "#CF8640", eye: 22, beard: "short" },
  old: { tip: -216, hair: "#ECEAE3", brow: "#B5AE9E", light: "#FFFFFF", eye: 20, beard: "long" },
};

type BuildLook = { head: number; shoulder: number; belly: number; hem: number };

/** What build sets: the head's width and the body's shoulders, belly and hem (half widths). */
const BY_BUILD: Record<Build, BuildLook> = {
  slim: { head: 0.93, shoulder: 92, belly: 100, hem: 104 },
  regular: { head: 1, shoulder: 108, belly: 134, hem: 128 },
  round: { head: 1.07, shoulder: 118, belly: 172, hem: 148 },
};

/** A beard's reach in the face's own coordinates: how far down and how far out. */
const BEARD_SIZE: Record<Exclude<Beard, "none">, { bottom: number; half: number }> = {
  short: { bottom: 404, half: 150 },
  medium: { bottom: 462, half: 160 },
  long: { bottom: 530, half: 166 },
};

export type Shape = (
  | { kind: "path"; d: string; fill: string; stroke?: string; width?: number }
  | { kind: "circle"; cx: number; cy: number; r: number; fill: string }
  | { kind: "group"; transform: string; children: Shape[] }
) & { part?: "badge" };

const FACE = "#FFD45E";
const EAR = "#F2B547";
const INK = "#3D2A16";
const CHEEK = "#FFAE7F";
const MOUTH = "#8B3A2A";
const WHITE = "#FFFFFF";
const TROUSERS = "#6D4C41";
const BOOTS = "#3E2723";
const BELT = "#5D3A1A";
const BUCKLE = "#F2B233";

function path(d: string, fill: string): Shape {
  return { kind: "path", d, fill };
}

function circle(cx: number, cy: number, r: number, fill: string): Shape {
  return { kind: "circle", cx, cy, r, fill };
}

function line(d: string, colour: string, width: number): Shape {
  return { kind: "path", d, fill: "none", stroke: colour, width };
}

/** A colour mixed with black (amount < 0) or white (amount > 0). */
function mix(hex: string, amount: number): string {
  const target = amount < 0 ? 0 : 255;
  const channels = [1, 3, 5].map((i) => {
    const value = Number.parseInt(hex.slice(i, i + 2), 16);
    return Math.round(value + (target - value) * Math.abs(amount));
  });
  return `#${channels.map((value) => value.toString(16).padStart(2, "0").toUpperCase()).join("")}`;
}

/** A round badge at the top right of the bust, with a mark drawn in white (drawn for a 512 square). */
function badge(age: Age, colour: string, mark: Shape[]): Shape[] {
  const [x, y, side] = BUST[age];
  const transform = `translate(${x} ${y}) scale(${+(side / 512).toFixed(4)})`;
  return [{ kind: "group", transform, children: [circle(440, 84, 56, colour), ...mark], part: "badge" }];
}

function signs(age: Age, state: HeinzelState): Shape[] {
  const mark = (colour: string, shapes: Shape[]) => badge(age, colour, shapes);
  switch (state) {
    case "sleeping":
      return mark("#5C6BC0", [line("M420 64 H460 L420 106 H460", WHITE, 12)]);
    case "thinking":
      return mark("#8E5BD8", [circle(414, 86, 9, WHITE), circle(440, 86, 9, WHITE), circle(466, 86, 9, WHITE)]);
    case "waiting":
      return mark("#1E88E5", [
        line("M424 70 Q424 50 440 50 Q458 50 458 68 Q458 82 440 88 L440 98", WHITE, 12),
        circle(440, 118, 7, WHITE),
      ]);
    case "blocked":
      return mark("#D9534F", [line("M440 52 L440 96", WHITE, 13), circle(440, 118, 8, WHITE)]);
    case "done":
      return mark("#2E9E5B", [line("M416 86 L434 104 L466 68", WHITE, 13)]);
    default:
      return [];
  }
}

function cap(tip: number, colour: string): Shape[] {
  const rise = 172 - tip;
  return [
    // The crown, over the whole top of the hair, so even the small cap sits snugly.
    path("M94 178 C94 84 168 38 256 38 C344 38 418 84 418 178 Z", colour),
    path(
      `M100 172 C110 ${172 - rise * 0.45} 180 ${tip + rise * 0.15} 270 ${tip} C306 ${tip - 14} 344 ${tip - 2} 362 ` +
        `${tip + 32} C336 ${tip + 22} 312 ${tip + 30} 304 ${tip + 50} C342 ${tip + rise * 0.45} 398 ` +
        `${172 - rise * 0.25} 412 172 Z`,
      colour,
    ),
    path(
      `M304 ${tip + 50} C342 ${tip + rise * 0.45} 398 ${172 - rise * 0.25} 412 172 L370 172 ` +
        `C360 ${172 - rise * 0.3} 330 ${tip + rise * 0.5} 292 ${tip + 70} Z`,
      mix(colour, -0.15),
    ),
    path(
      "M88 176 C150 136 362 136 424 176 C428 196 414 206 398 200 C340 176 172 176 114 200 C98 206 84 196 88 176 Z",
      mix(colour, -0.1),
    ),
  ];
}

function beardShape(beard: Exclude<Beard, "none">, colour: string): Shape {
  const { bottom: b, half: w } = BEARD_SIZE[beard];
  const [l, r, mid] = [256 - w, 256 + w, 242 + (b - 242) * 0.55];
  return path(
    `M${l} 242 C${l - 10} ${mid} ${256 - w * 0.55} ${b - 18} 256 ${b} C${256 + w * 0.55} ${b - 18} ${r + 10} ` +
      `${mid} ${r} 242 C${r - 30} 300 ${r - 70} 318 256 318 C${l + 70} 318 ${l + 30} 300 ${l} 242 Z`,
    colour,
  );
}

function eyes(state: HeinzelState, size: number): Shape[] {
  if (state === "sleeping")
    return [line("M180 276 Q202 292 224 276", INK, 10), line("M288 276 Q310 292 332 276", INK, 10)];
  if (state === "done") return [line("M180 282 Q202 258 224 282", INK, 10), line("M288 282 Q310 258 332 282", INK, 10)];
  const up = state === "thinking" ? -10 : 0;
  return [
    circle(202, 276 + up, size, INK),
    circle(310, 276 + up, size, INK),
    circle(202 + size * 0.35, 266 + up, size * 0.35, WHITE),
    circle(310 + size * 0.35, 266 + up, size * 0.35, WHITE),
  ];
}

function mouth(state: HeinzelState, bearded: boolean, light: string): Shape[] {
  if (!bearded) {
    if (state === "blocked") return [line("M222 352 Q256 332 290 352", MOUTH, 10)];
    if (state === "sleeping") return [circle(256, 342, 12, MOUTH)];
    return [
      path(state === "done" ? "M198 318 C208 384 304 384 314 318 Z" : "M206 322 C216 372 296 372 306 322 Z", MOUTH),
    ];
  }
  const lips =
    state === "blocked"
      ? line("M226 354 Q256 336 286 354", MOUTH, 10)
      : path(state === "done" ? "M214 334 C222 382 290 382 298 334 Z" : "M222 336 C230 368 282 368 290 336 Z", MOUTH);
  const moustache = path(
    "M256 308 C236 298 202 302 192 322 C212 336 240 328 256 316 C272 328 300 336 320 322 C310 302 276 298 256 308 Z",
    light,
  );
  return [lips, moustache];
}

/** The head in the face's own 512 coordinates (centre 256,236, radius 158): ears, face, hair, beard, cap, features. */
function head(age: Age, beard: Beard, hat: Hat, state: HeinzelState): Shape[] {
  const look = BY_AGE[age];
  const brow = age === "old" ? 14 : 10;
  return [
    path("M112 226 C82 222 54 230 46 248 C40 264 58 280 86 286 C98 288 108 288 114 286 Z", FACE),
    path("M400 226 C430 222 458 230 466 248 C472 264 454 280 426 286 C414 288 404 288 398 286 Z", FACE),
    path("M108 240 C88 238 70 244 66 254 C64 264 76 272 94 274 C100 275 106 275 110 274 Z", EAR),
    path("M404 240 C424 238 442 244 446 254 C448 264 436 272 418 274 C412 275 406 275 402 274 Z", EAR),
    circle(256, 236, 158, FACE),
    path(
      "M96 246 C84 120 168 48 256 48 C344 48 428 120 416 246 C406 210 386 186 362 174 Q342 218 302 182 " +
        "Q256 226 210 182 Q170 218 150 174 C126 186 106 210 96 246 Z",
      look.hair,
    ),
    ...(beard === "none" ? [] : [beardShape(beard, look.hair)]),
    ...(look.tip === null ? [] : cap(look.tip, HATS[hat])),
    circle(160, 318, 24, CHEEK),
    circle(352, 318, 24, CHEEK),
    // Brows: soft and raised; worried when blocked.
    ...(state === "blocked"
      ? [line("M176 230 L224 214", look.brow, 11), line("M288 214 L336 230", look.brow, 11)]
      : [line("M178 232 Q202 216 226 230", look.brow, brow), line("M286 230 Q310 216 334 232", look.brow, brow)]),
    ...eyes(state, look.eye),
    ...mouth(state, beard !== "none", look.light),
  ];
}

/** A sleeve from the shoulder to a hand at (x, y). */
function arm(shoulder: number, x: number, y: number, sleeve: string): Shape[] {
  const out = x < 256 ? -1 : 1;
  return [
    path(
      `M${shoulder - out * 16} 486 C${shoulder + out * 20} 520 ${x + out * 14} ${y - 50} ${x + out * 18} ${y} ` +
        `L${x - out * 18} ${y} C${x - out * 10} ${y - 50} ${shoulder - out * 30} 540 ${shoulder - out * 40} 500 Z`,
      sleeve,
    ),
    circle(x, y + 8, 24, FACE),
  ];
}

/** The arms for a state: down, holding a tray, a hand raised or at the chin, or together in a little bow. */
function arms(state: HeinzelState, sleeve: string, build: BuildLook): Shape[] {
  const [left, right] = [256 - build.shoulder, 256 + build.shoulder];
  const down = [...arm(left, 256 - build.belly - 12, 640, sleeve), ...arm(right, 256 + build.belly + 12, 640, sleeve)];
  switch (state) {
    case "working":
      // A tray with a mug of something good, carried in both hands: busy helping.
      return [
        ...arm(left, 170, 580, sleeve),
        ...arm(right, 342, 580, sleeve),
        path("M120 572 H392 Q404 572 404 584 Q404 596 392 596 H120 Q108 596 108 584 Q108 572 120 572 Z", "#9AA0A6"),
        path("M228 506 H284 V562 Q284 572 274 572 H238 Q228 572 228 562 Z", BUCKLE),
        line("M284 518 Q308 518 308 538 Q308 556 284 556", "#C98A1E", 8),
        path("M222 512 Q222 490 242 494 Q252 480 266 490 Q290 486 288 512 Z", WHITE),
      ];
    case "waiting":
      // The right hand raised beside the head: "I need you".
      return [
        ...arm(left, 256 - build.belly - 12, 640, sleeve),
        path(
          `M${right - 20} 490 C${right + 30} 470 ${right + 60} 430 ${right + 66} 392 L${right + 30} 384 ` +
            `C${right + 24} 420 ${right} 450 ${right - 40} 470 Z`,
          sleeve,
        ),
        circle(right + 50, 376, 26, FACE),
      ];
    case "thinking":
      return [...arm(left, 256 - build.belly - 12, 640, sleeve), ...arm(right, 306, 452, sleeve)];
    case "done":
      return [...arm(left, 226, 600, sleeve), ...arm(right, 286, 600, sleeve)];
    default:
      return down;
  }
}

function legs(sitting: boolean, hem: number): Shape[] {
  const leg = Math.round(hem * 0.7);
  if (sitting)
    return [
      path(
        `M${256 - leg - 20} 680 H${256 + leg + 20} V744 Q${256 + leg + 20} 760 ${256 + leg + 4} 760 H${256 - leg - 4} ` +
          `Q${256 - leg - 20} 760 ${256 - leg - 20} 744 Z`,
        TROUSERS,
      ),
      path(`M${256 - leg - 30} 800 Q${256 - leg - 30} 756 ${256 - leg + 14} 756 H248 V800 Z`, BOOTS),
      path(`M${256 + leg + 30} 800 Q${256 + leg + 30} 756 ${256 + leg - 14} 756 H264 V800 Z`, BOOTS),
    ];
  return [
    path(`M${256 - leg} 680 H250 V804 H${256 - leg + 6} Z`, TROUSERS),
    path(`M${256 + leg} 680 H262 V804 H${256 + leg - 6} Z`, TROUSERS),
    path(`M${256 - leg - 22} 846 Q${256 - leg - 22} 800 ${256 - leg + 16} 800 H250 V846 Z`, BOOTS),
    path(`M${256 + leg + 22} 846 Q${256 + leg + 22} 800 ${256 + leg - 16} 800 H262 V846 Z`, BOOTS),
  ];
}

/** The whole Heinzelmännchen on the 512 by 860 canvas, back to front; the top 512 square is its bust. */
export function heinzelShapes({
  age = "grown",
  beard,
  hat = "red",
  shirt = "green",
  build = "regular",
  state = "idle",
}: HeinzelLook = {}): Shape[] {
  const chin: Beard = age === "baby" ? "none" : (beard ?? BY_AGE[age].beard);
  const b = BY_BUILD[build];
  const colour = SHIRTS[shirt];
  const [sl, sr] = [256 - b.shoulder, 256 + b.shoulder];
  const [bl, br, hl, hr] = [256 - b.belly, 256 + b.belly, 256 - b.hem, 256 + b.hem];
  const scale = 0.75;
  const bow = state === "done" ? 18 : 0;
  return [
    ...legs(state === "sleeping", b.hem),
    path(
      `M${sl} 500 C${sl} 472 ${256 - b.shoulder * 0.5} 462 256 462 C${256 + b.shoulder * 0.5} 462 ${sr} 472 ${sr} 500 ` +
        `C${br} 560 ${br} 640 ${hr} 690 Q256 708 ${hl} 690 C${bl} 640 ${bl} 560 ${sl} 500 Z`,
      colour,
    ),
    path("M200 466 C216 506 296 506 312 466 C290 476 222 476 200 466 Z", mix(colour, 0.25)),
    path(`M${bl + 6} 622 H${br - 6} V648 H${bl + 6} Z`, BELT),
    path("M238 618 H274 V652 H238 Z M248 628 V642 H264 V628 Z", BUCKLE),
    ...arms(state, mix(colour, -0.08), b),
    {
      kind: "group",
      transform: `matrix(${+(scale * b.head).toFixed(4)} 0 0 ${scale} ${+(256 - 256 * scale * b.head).toFixed(2)} ${
        350 - 236 * scale + bow
      })`,
      children: head(age, chin, hat, state),
    },
    ...signs(age, state),
  ];
}

/** One shape as SVG markup, indented. */
export function svgShape(shape: Shape, indent = "  "): string {
  if (shape.kind === "group")
    return [
      `${indent}<g transform="${shape.transform}">`,
      ...shape.children.map((child) => svgShape(child, `${indent}  `)),
      `${indent}</g>`,
    ].join("\n");
  if (shape.kind === "circle")
    return `${indent}<circle cx="${shape.cx}" cy="${shape.cy}" r="${shape.r}" fill="${shape.fill}"/>`;
  const stroke = shape.stroke
    ? ` stroke="${shape.stroke}" stroke-width="${shape.width}" stroke-linecap="round" stroke-linejoin="round"`
    : "";
  return `${indent}<path d="${shape.d}" fill="${shape.fill}"${stroke}/>`;
}

const COMMENT = [
  "  <!-- Heinzelmännchen, an original drawing made by web/src/components/heinzel.ts (`make icons`); never an emoji",
  "       font's artwork. The favicon, the app icons, the theme's logo and the notifier's icon are made from it. -->",
];

function svg(viewBox: string, body: string[]): string {
  const open = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${viewBox}" role="img" aria-label="Heinzelmännchen">`;
  return [open, ...COMMENT, ...body, "</svg>", ""].join("\n");
}

/** One Heinzelmännchen as an SVG file; with no look and the bust, the app's icon (heinzel.svg). */
export function heinzelSvg(look: HeinzelLook = {}, view: View = "bust"): string {
  return svg(
    viewBox(look.age, view),
    heinzelShapes(look).map((shape) => svgShape(shape)),
  );
}

/** The product's logo: a busy swarm walking together, mostly young and grown, with one baby and one old one. */
export const SWARM: { look: HeinzelLook; x: number; y: number; scale: number }[] = [
  { look: { age: "old", hat: "purple", shirt: "slate", build: "slim" }, x: 12, y: 40, scale: 0.4 },
  { look: { age: "young", hat: "teal", shirt: "rose", build: "slim" }, x: 296, y: 40, scale: 0.4 },
  { look: { age: "baby", shirt: "ochre" }, x: -14, y: 168, scale: 0.4 },
  { look: { age: "young", hat: "rust", shirt: "blue", state: "working" }, x: 322, y: 168, scale: 0.4 },
  { look: { age: "grown", hat: "red", shirt: "green" }, x: 140, y: 120, scale: 0.46 },
];

/** The swarm as an SVG file (heinzel-swarm.svg). */
export function heinzelSwarmSvg(): string {
  const body = SWARM.flatMap(({ look, x, y, scale }) => [
    `  <g transform="translate(${x} ${y}) scale(${scale})">`,
    ...heinzelShapes(look)
      .filter((shape) => shape.part !== "badge")
      .map((shape) => svgShape(shape, "    ")),
    "  </g>",
  ]);
  return svg("0 0 512 512", body);
}

/** Four of them dancing in a ring while hz starts (loading-dance.svg): each bobs and sways a beat after the last. */
export const DANCERS: { look: HeinzelLook; angle: number }[] = [
  { look: { age: "grown", hat: "red", shirt: "green" }, angle: 270 },
  { look: { age: "young", hat: "teal", shirt: "rose", build: "slim" }, angle: 0 },
  { look: { age: "young", hat: "rust", shirt: "blue", state: "working" }, angle: 180 },
  { look: { age: "old", hat: "purple", shirt: "slate", build: "slim" }, angle: 90 },
];

/** The dance as an SVG file: pure SVG and CSS, a 1.2 s loop that stands still when the viewer prefers less motion. */
export function heinzelDanceSvg(): string {
  const scale = 0.2;
  const style = [
    "  <style>",
    "    .dancer { transform-box: fill-box; transform-origin: 50% 100%; }",
    "    @media (prefers-reduced-motion: no-preference) {",
    "      .dancer { animation: hz-dance 1.2s ease-in-out infinite; }",
    "      .d1 { animation-delay: -0.9s; } .d2 { animation-delay: -0.6s; } .d3 { animation-delay: -0.3s; }",
    "    }",
    "    @keyframes hz-dance {",
    "      0%, 100% { transform: translateY(0) rotate(-4deg); }",
    "      50% { transform: translateY(-16px) rotate(4deg); }",
    "    }",
    "  </style>",
  ];
  const body = DANCERS.flatMap(({ look, angle }, index) => {
    const x = 256 + 175 * Math.cos((angle * Math.PI) / 180) - 256 * scale;
    const y = 285 + 62 * Math.sin((angle * Math.PI) / 180) - 860 * scale;
    return [
      `  <g transform="translate(${Math.round(x)} ${Math.round(y)}) scale(${scale})">`,
      `    <g class="dancer d${index}">`,
      ...heinzelShapes(look)
        .filter((shape) => shape.part !== "badge")
        .map((shape) => svgShape(shape, "      ")),
      "    </g>",
      "  </g>",
    ];
  });
  return svg("0 0 512 360", [...style, ...body]);
}
