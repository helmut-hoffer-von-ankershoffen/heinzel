// Heinzelmännchen's picture, an original drawing: a cheerful gnome, head and shoulders, with a tall red-orange cap
// whose tip flops, rosy nose and cheeks, smiling eyes and a tunic. Each Heinzelmännchen can have its own tunic colour,
// age and beard, and shows its state (sleeping, working, thinking, waiting for you, blocked, done).
//
// `make icons` writes two files from here, and everything else is made from those: heinzel.svg, the classic one alone
// (old, long white beard, green; the favicon and the small icons), and heinzel-group.svg, three of them together (the
// product's logo where it is large enough to read). Plain TypeScript with no imports, so node runs this file as is.

/** The tunic colours a Heinzelmännchen can wear, each at least 3:1 against both a light and a dark background. */
export const TUNICS = {
  green: "#43A047",
  blue: "#1E88E5",
  purple: "#8E5BD8",
  "red-brown": "#B5532F",
  ochre: "#B7841C",
  teal: "#1F9488",
  slate: "#64748B",
  rose: "#D9567C",
} as const;
export type Tunic = keyof typeof TUNICS;

export const AGES = ["young", "grown", "old"] as const;
export type Age = (typeof AGES)[number];
export const BEARDS = ["none", "short", "medium", "long"] as const;
export type Beard = (typeof BEARDS)[number];
export const STATES = ["idle", "sleeping", "working", "thinking", "waiting", "blocked", "done"] as const;
export type HeinzelState = (typeof STATES)[number];

/** How a Heinzelmännchen looks; the beard follows the age unless it is given. */
export type HeinzelLook = { tunic?: Tunic; age?: Age; beard?: Beard; state?: HeinzelState };

/** What age changes: the face's roundness, the cap's height and the colour of hair and beard. */
const BY_AGE: Record<Age, { face: number; tip: number; hair: string; light: string; line: string; beard: Beard }> = {
  young: { face: 120, tip: 112, hair: "#C98A3A", light: "#DDA457", line: "#94601F", beard: "none" },
  grown: { face: 114, tip: 78, hair: "#A3A6AB", light: "#C4C7CB", line: "#767A81", beard: "short" },
  old: { face: 112, tip: 46, hair: "#ECEAE3", light: "#FFFFFF", line: "#B5AE9E", beard: "long" },
};

/** How far a beard reaches down and out. */
const BEARD_SIZE: Record<Exclude<Beard, "none">, { bottom: number; half: number }> = {
  short: { bottom: 404, half: 108 },
  medium: { bottom: 446, half: 116 },
  long: { bottom: 488, half: 122 },
};

/** One shape on the 512 by 512 square; `part` names what the web app may move (never with reduced motion). */
export type Shape = (
  | { kind: "path"; d: string; fill: string; stroke?: string; width?: number }
  | { kind: "circle"; cx: number; cy: number; r: number; fill: string }
) & { part?: "badge" };

const INK = "#3D2A16";
const SKIN = "#F6C9A0";
const CHEEK = "#F4A08A";
const NOSE = "#E9806A";
const CAP = "#E8572A";
const CAP_SHADE = "#C9431F";
const BRIM = "#D24B23";
const MOUTH = "#8B3A2A";
const WHITE = "#FFFFFF";

function path(d: string, fill: string, stroke?: string, width?: number): Shape {
  return stroke ? { kind: "path", d, fill, stroke, width } : { kind: "path", d, fill };
}

function circle(cx: number, cy: number, r: number, fill: string): Shape {
  return { kind: "circle", cx, cy, r, fill };
}

/** A round badge at the top right, behind the cap's tip, with a mark drawn in white. */
function badge(colour: string, mark: Shape[]): Shape[] {
  return [circle(436, 96, 60, colour), ...mark].map((shape) => ({ ...shape, part: "badge" as const }));
}

function eyes(state: HeinzelState): Shape[] {
  switch (state) {
    case "sleeping":
      return [path("M196 294 Q214 308 232 294", "none", INK, 10), path("M280 294 Q298 308 316 294", "none", INK, 10)];
    case "thinking":
      // Open eyes looking up and aside, as when working something out.
      return [
        circle(214, 296, 15, WHITE),
        circle(298, 296, 15, WHITE),
        circle(220, 289, 9, INK),
        circle(304, 289, 9, INK),
      ];
    case "blocked":
      // Open eyes under brows raised in the middle: worried, not cross.
      return [
        circle(214, 300, 10, INK),
        circle(298, 300, 10, INK),
        path("M194 278 L230 266", "none", INK, 9),
        path("M282 266 L318 278", "none", INK, 9),
      ];
    case "waiting":
      return [circle(214, 298, 11, INK), circle(298, 298, 11, INK)];
    default:
      return [path("M196 300 Q214 282 232 300", "none", INK, 10), path("M280 300 Q298 282 316 300", "none", INK, 10)];
  }
}

function signs(state: HeinzelState): Shape[] {
  switch (state) {
    case "sleeping":
      return badge("#5C6BC0", [path("M414 74 H456 L414 118 H456", "none", WHITE, 12)]);
    case "thinking":
      return badge("#8E5BD8", [circle(410, 104, 9, WHITE), circle(436, 104, 9, WHITE), circle(462, 104, 9, WHITE)]);
    case "waiting":
      return badge("#1E88E5", [
        path("M420 82 Q420 62 436 62 Q454 62 454 80 Q454 94 436 100 L436 110", "none", WHITE, 12),
        circle(436, 130, 7, WHITE),
      ]);
    case "blocked":
      return badge("#D9534F", [path("M436 64 L436 108", "none", WHITE, 13), circle(436, 130, 8, WHITE)]);
    case "done":
      return badge("#2E9E5B", [path("M412 98 L430 116 L462 80", "none", WHITE, 13)]);
    case "working":
      // A tray with a mug of something good: busy helping.
      return [
        circle(352, 478, 20, SKIN),
        path("M326 456 H486 Q496 456 496 466 Q496 476 486 476 H326 Q316 476 316 466 Q316 456 326 456 Z", "#9AA0A6"),
        path("M392 392 H440 V448 Q440 456 432 456 H400 Q392 456 392 448 Z", "#F2B233"),
        path("M440 404 Q462 404 462 422 Q462 440 440 440", "none", "#C98A1E", 8),
        path("M386 396 Q386 376 404 380 Q414 366 428 376 Q448 372 446 396 Z", WHITE),
      ];
    default:
      return [];
  }
}

/** The gnome's shapes on a 512 by 512 square, back to front. */
export function heinzelShapes({ tunic = "green", age = "old", beard, state = "idle" }: HeinzelLook = {}): Shape[] {
  const look = BY_AGE[age];
  const chin = beard ?? look.beard;
  const t = look.tip;
  const shapes: Shape[] = [
    path("M92 512 C92 436 164 400 256 400 C348 400 420 436 420 512 Z", TUNICS[tunic]),
    // Hair at the sides, under the cap's brim.
    path("M118 276 C108 312 116 346 140 362 C150 330 156 300 160 280 Z", look.hair),
    path("M394 276 C404 312 396 346 372 362 C362 330 356 300 352 280 Z", look.hair),
    circle(256, 292, look.face, SKIN),
  ];
  if (chin !== "none") {
    const { bottom: b, half: w } = BEARD_SIZE[chin];
    const [l, r, mid] = [256 - w, 256 + w, 296 + (b - 296) * 0.55];
    shapes.push(
      path(
        `M${l} 296 C${l - 8} ${mid} ${256 - w * 0.55} ${b - 14} 256 ${b} C${256 + w * 0.55} ${b - 14} ${r + 8} ${mid} ` +
          `${r} 296 C${r - 32} 340 302 354 256 354 C210 354 ${l + 32} 340 ${l} 296 Z`,
        look.hair,
        look.line,
        6,
      ),
    );
  }
  shapes.push(
    // The cap: tall, its tip flopping to the right, a shaded side and a brim.
    path(
      `M116 268 C126 ${t + 130} 186 ${t + 40} 266 ${t} C300 ${t - 16} 338 ${t - 6} 356 ${t + 24} ` +
        `C330 ${t + 16} 306 ${t + 24} 298 ${t + 42} C336 ${t + 104} 384 ${t + 160} 396 268 Z`,
      CAP,
    ),
    path(
      `M298 ${t + 42} C336 ${t + 104} 384 ${t + 160} 396 268 L356 268 C344 ${t + 160} 316 ${t + 94} 288 ${t + 58} Z`,
      CAP_SHADE,
    ),
    path(
      "M104 270 C160 234 352 234 408 270 C412 288 400 298 386 294 C330 270 182 270 126 294 C112 298 100 288 104 270 Z",
      BRIM,
    ),
    circle(186, 324, 22, CHEEK),
    circle(326, 324, 22, CHEEK),
    ...eyes(state),
  );
  const happy = state === "done";
  if (chin === "none") {
    shapes.push(
      state === "blocked"
        ? path("M226 378 Q256 360 286 378", "none", MOUTH, 9)
        : path(happy ? "M214 356 C222 404 290 404 298 356 Z" : "M222 360 C230 390 282 390 290 360 Z", MOUTH),
    );
  } else {
    if (happy) shapes.push(path("M230 364 C236 396 276 396 282 364 Z", MOUTH));
    shapes.push(
      path(
        "M256 342 C232 332 192 338 180 362 C204 376 238 368 256 352 C274 368 308 376 332 362 C320 338 280 332 256 342 Z",
        look.light,
        look.line,
        5,
      ),
    );
  }
  shapes.push(circle(256, 326, 26, NOSE), ...signs(state));
  return shapes;
}

/** One shape as SVG markup. */
export function svgShape(shape: Shape): string {
  if (shape.kind === "circle") return `<circle cx="${shape.cx}" cy="${shape.cy}" r="${shape.r}" fill="${shape.fill}"/>`;
  const stroke = shape.stroke
    ? ` stroke="${shape.stroke}" stroke-width="${shape.width}" stroke-linecap="round" stroke-linejoin="round"`
    : "";
  return `<path d="${shape.d}" fill="${shape.fill}"${stroke}/>`;
}

/** The product's logo: three of them together, the classic one in front. Each with where it stands and its size. */
export const GROUP: { look: HeinzelLook; x: number; y: number; scale: number }[] = [
  { look: { tunic: "blue", age: "grown", beard: "medium" }, x: -72, y: 133, scale: 0.74 },
  { look: { tunic: "purple", age: "young" }, x: 205, y: 133, scale: 0.74 },
  { look: {}, x: 20.5, y: 41, scale: 0.92 },
];

const HEADER = [
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" role="img" aria-label="Heinzelmännchen">',
  "  <!-- Heinzelmännchen, an original drawing made by web/src/components/heinzel.ts (`make icons`); never an emoji",
  "       font's artwork. The favicon, the app icons, the theme's logo and the notifier's icon are made from it. -->",
];

/** A gnome as an SVG file; with no look, the app's icon (heinzel.svg). */
export function heinzelSvg(look: HeinzelLook = {}): string {
  return [...HEADER, ...heinzelShapes(look).map((shape) => `  ${svgShape(shape)}`), "</svg>", ""].join("\n");
}

/** The product's logo as an SVG file (heinzel-group.svg). */
export function heinzelGroupSvg(): string {
  const groups = GROUP.flatMap(({ look, x, y, scale }) => [
    `  <g transform="translate(${x} ${y}) scale(${scale})">`,
    ...heinzelShapes(look).map((shape) => `    ${svgShape(shape)}`),
    "  </g>",
  ]);
  return [...HEADER, ...groups, "</svg>", ""].join("\n");
}
