// Heinzelmännchen's picture, an original drawing in a flat, friendly emoji style: a round yellow face with big shiny
// eyes, rosy cheeks and a smile, rounded ears, a scalloped fringe, a shirt with a belt, trousers and boots. A person
// styles each Heinzelmännchen with five choices: its age (which also sets the cap: none for a baby, then small, medium
// and tall), its beard, its hat's colour, its shirt's colour and its build. Its state shows in its face, its posture
// and a small badge (asleep, working, thinking, waiting for you, blocked, done).
//
// Accessories are data on top of the look: a list from a closed set (umbrella, sunglasses, scarf) that anything can
// set, e.g. a weather plugin dressing a Heinzelmännchen for rain, sun and cold. `parseAccessories` validates it (unknown
// names are dropped, duplicates collapse), the drawing layers each accessory onto the full figure and the bust, and
// every age and build wears every accessory, a baby included (see ACCESSORIES). With none, the drawing is unchanged.
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
/**
 * What a Heinzelmännchen can wear or carry beyond its five choices, in the order they are layered. A closed set:
 * adding one is a code change, never something a plugin invents. Every age and build wears all of them. A baby too:
 * baby sunglasses and a baby umbrella are real and cute, the drawing scales them with the head, and a baby's cap-less
 * head leaves the umbrella and the scarf nothing to collide with.
 */
export const ACCESSORIES = ["umbrella", "sunglasses", "scarf"] as const;
export type Accessory = (typeof ACCESSORIES)[number];

/** How a screen reader hears each accessory, appended to the avatar's name when it has one. */
export const ACCESSORY_LABEL: Record<Accessory, string> = {
  umbrella: "with an umbrella",
  sunglasses: "wearing sunglasses",
  scarf: "wearing a scarf",
};

export function isAccessory(value: unknown): value is Accessory {
  return typeof value === "string" && (ACCESSORIES as readonly string[]).includes(value);
}

/**
 * Accessories from untrusted data (a plugin's contribution, a stored profile): the known names once each, in layering
 * order. Anything else (an unknown name, a non-string, a non-list) is dropped, so bad data dresses nobody rather than
 * breaking the picture.
 */
export function parseAccessories(value: unknown): Accessory[] {
  const given = Array.isArray(value) ? value : [];
  return ACCESSORIES.filter((accessory) => given.includes(accessory));
}

export const VIEWS = ["full", "bust", "head"] as const;
export type View = (typeof VIEWS)[number];

/** The bust's square (x, y, side) on the canvas: the head, its cap and the shoulders, tight for each age's cap. */
const BUST: Record<Age, [number, number, number]> = {
  baby: [95, 190, 322],
  young: [80, 160, 352],
  grown: [47.5, 95, 417],
  old: [0, 0, 512],
};

/** The umbrella's canopy: half width, height, and how far above the cap's top its rim sits (behind the head). */
const CANOPY = { half: 170, height: 84, rim: -40 };
/** Where the umbrella's pole is held, and where its canopy is centred horizontally. */
const GRIP = { x: 430, y: 600, canopyX: 335 };

/** The canvas y of the top of the head's drawing (the cap's tip, or the crown for a baby). */
function headTop(age: Age): number {
  return 173 + 0.75 * Math.min(BY_AGE[age].tip ?? 38, 38);
}

/** The canvas y of the top of the umbrella's canopy for this age, with room for its tilt. */
function umbrellaTop(age: Age): number {
  return Math.floor(headTop(age) - CANOPY.rim - CANOPY.height - 14 - 70);
}

/**
 * The viewBox of a Heinzelmännchen of this age: the whole figure, or its bust. An umbrella reaches above the head, so
 * with one the box grows upwards to hold it (the bust stays square).
 */
export function viewBox(age: Age = "grown", view: View = "full", accessories: readonly Accessory[] = []): string {
  const top = parseAccessories(accessories).includes("umbrella") ? umbrellaTop(age) : Number.POSITIVE_INFINITY;
  if (view === "full") {
    const y = Math.min(0, top);
    return `0 ${y} 512 ${860 - y}`;
  }
  const [x, y, side] = BUST[age];
  // The head: the bust without most of its shoulders, for the tiniest slots.
  if (view === "head") return `${x + side * 0.12} ${y + side * 0.03} ${side * 0.76} ${side * 0.76}`;
  const reach = accessories.includes("umbrella") ? GRIP.canopyX + CANOPY.half + 10 : 0;
  const grow = Math.max(0, y - top, 2 * (reach - (x + side)));
  return `${x - grow / 2} ${y - grow} ${side + grow} ${side + grow}`;
}

/** How a Heinzelmännchen looks. A baby has no beard, whatever is asked. */
export type HeinzelLook = {
  age?: Age;
  beard?: Beard;
  hat?: Hat;
  shirt?: Shirt;
  build?: Build;
  state?: HeinzelState;
  /** Accessories to layer on, data only; unknown names are ignored (parseAccessories). */
  accessories?: readonly Accessory[];
  theme?: ThemeLook;
};

/** A theme dresses defaults; explicit personal colours still win. No arbitrary graphics or CSS. */
export type ThemeLook = {
  palette?: Partial<Record<"hat" | "shirt" | "scarf", string>>;
  decorations?: readonly "snow"[];
};

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
) & { part?: Part; accessory?: Accessory };

/** Parts the avatar can move on their own (idle life): the state's badge, the eyes, the hat, the waving hand. */
export type Part = "badge" | "eyes" | "hat" | "wave";

/** The shapes tagged as one part, e.g. all of a cap's pieces, so they move together. */
function tag(part: Part, shapes: Shape[]): Shape[] {
  return shapes.map((shape) => ({ ...shape, part }));
}

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
  if (hex.length === 4) hex = `#${[...hex.slice(1)].map((value) => value + value).join("")}`;
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

const FRAME = "#2B2B38";
const LENS = "#171722";
const GLINT = "#9AA5CE";
const CANOPY_COLOUR = "#2F80ED";
const POLE = "#5D4037";
const SCARF = "#C0392B";
const SCARF_STRIPE = "#F3E5C8";

/** Wraps shapes as one accessory's layer, so a drawing can say (and a test check) which shapes it added. */
function layer(accessory: Accessory, children: Shape[], transform = "translate(0 0)"): Shape {
  return { kind: "group", transform, children, accessory };
}

/** Sunglasses over the eyes, in the head's own coordinates: two dark lenses, a bridge, arms to the ears, a glint. */
function sunglasses(): Shape {
  const lens = (cx: number) => {
    const [l, r] = [cx - 52, cx + 52];
    return path(
      `M${l} 264 Q${l} 250 ${l + 14} 250 H${r - 14} Q${r} 250 ${r} 264 V286 Q${r} 312 ${r - 26} 312 H${l + 26} ` +
        `Q${l} 312 ${l} 286 Z`,
      LENS,
    );
  };
  return layer("sunglasses", [
    line("M150 262 L102 254", FRAME, 8),
    line("M362 262 L410 254", FRAME, 8),
    lens(202),
    lens(310),
    line("M252 266 Q256 258 260 266", FRAME, 8),
    line("M170 296 L188 268", GLINT, 6),
    line("M278 296 L296 268", GLINT, 6),
  ]);
}

/** The umbrella, held in the right hand and tilted over the head; drawn behind the head and arm, so the cap shows under it. */
function umbrella(age: Age): Shape {
  const centre = { x: GRIP.canopyX, y: headTop(age) - CANOPY.rim };
  const [dx, dy] = [centre.x - GRIP.x, GRIP.y - centre.y];
  const length = Math.round(Math.hypot(dx, dy));
  const angle = +((Math.atan2(dx, dy) * 180) / Math.PI).toFixed(2);
  const { half: w, height: h } = CANOPY;
  const third = Math.round(w / 3);
  const dome = path(
    `M${-w} 0 C${-w} ${-h * 0.75} ${-w * 0.45} ${-h} 0 ${-h} C${w * 0.45} ${-h} ${w} ${-h * 0.75} ${w} 0 ` +
      `Q${third * 2} 22 ${third} 0 Q0 22 ${-third} 0 Q${-third * 2} 22 ${-w} 0 Z`,
    CANOPY_COLOUR,
  );
  const rib = mix(CANOPY_COLOUR, -0.25);
  return layer(
    "umbrella",
    [
      line(`M0 0 V${-length + 4}`, POLE, 9),
      group(`translate(0 ${-length})`, [
        dome,
        line(`M0 ${-h} L${-third} 0`, rib, 5),
        line(`M0 ${-h} L${third} 0`, rib, 5),
        circle(0, -h - 6, 8, POLE),
      ]),
    ],
    `translate(${GRIP.x} ${GRIP.y}) rotate(${angle})`,
  );
}

function group(transform: string, children: Shape[]): Shape {
  return { kind: "group", transform, children };
}

/** A scarf wound round the neck, over the beard, with a striped tail hanging down the shirt. */
function scarf(b: BuildLook, colour = SCARF): Shape {
  const [sl, sr] = [256 - b.shoulder, 256 + b.shoulder];
  return layer("scarf", [
    path(`M296 490 L330 494 L338 612 L302 616 Z`, mix(colour, -0.15)),
    line("M300 548 L335 551", SCARF_STRIPE, 9),
    line("M301 580 L336 583", SCARF_STRIPE, 9),
    path(`M${sl + 6} 468 Q256 484 ${sr - 6} 468 L${sr - 2} 492 Q256 516 ${sl + 2} 492 Z`, colour),
    line(`M${sl + 38} 478 Q${sl + 44} 494 ${sl + 40} 500`, SCARF_STRIPE, 8),
    line(`M${sr - 38} 478 Q${sr - 44} 494 ${sr - 40} 500`, SCARF_STRIPE, 8),
  ]);
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
function head(age: Age, beard: Beard, hat: string, state: HeinzelState, glasses: boolean, snow: boolean): Shape[] {
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
    ...(look.tip === null
      ? []
      : tag("hat", [
          ...cap(look.tip, hat),
          ...(snow
            ? [
                path(
                  `M228 ${look.tip + 22} Q264 ${look.tip - 11} 302 ${look.tip + 3} L310 ${look.tip + 22} Q266 ${look.tip + 17} 234 ${look.tip + 38} Z`,
                  WHITE,
                ),
              ]
            : []),
        ])),
    circle(160, 318, 24, CHEEK),
    circle(352, 318, 24, CHEEK),
    // Brows: soft and raised; worried when blocked.
    ...(state === "blocked"
      ? [line("M176 230 L224 214", look.brow, 11), line("M288 214 L336 230", look.brow, 11)]
      : [line("M178 232 Q202 216 226 230", look.brow, brow), line("M286 230 Q310 216 334 232", look.brow, brow)]),
    ...tag("eyes", eyes(state, look.eye)),
    ...(glasses ? [sunglasses()] : []),
    ...mouth(state, beard !== "none", look.light),
    ...(snow ? [circle(118, 140, 9, WHITE), circle(394, 105, 7, WHITE), circle(370, 360, 6, WHITE)] : []),
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
function arms(state: HeinzelState, sleeve: string, build: BuildLook, holding: boolean): Shape[] {
  const [left, right] = [256 - build.shoulder, 256 + build.shoulder];
  const rightDown = holding ? arm(right, GRIP.x, GRIP.y, sleeve) : arm(right, 256 + build.belly + 12, 640, sleeve);
  // The right hand down is the one that waves in idle life.
  const down = [...arm(left, 256 - build.belly - 12, 640, sleeve), ...(holding ? rightDown : tag("wave", rightDown))];
  // The right hand holds the umbrella's pole, so it neither waves nor stays down; a working one carries the tray in
  // its left hand alone.
  switch (holding && state === "waiting" ? "idle" : state) {
    case "working":
      // A tray with a mug of something good, carried in both hands: busy helping.
      return [
        ...arm(left, 170, 580, sleeve),
        ...(holding ? rightDown : arm(right, 342, 580, sleeve)),
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
      return [
        ...arm(left, 256 - build.belly - 12, 640, sleeve),
        ...(holding ? rightDown : arm(right, 306, 452, sleeve)),
      ];
    case "done":
      return [...arm(left, 226, 600, sleeve), ...(holding ? rightDown : arm(right, 286, 600, sleeve))];
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
  hat,
  shirt,
  build = "regular",
  state = "idle",
  accessories,
  theme,
}: HeinzelLook = {}): Shape[] {
  const snow = theme?.decorations?.includes("snow") ?? false;
  const worn = parseAccessories([...(accessories ?? []), ...(snow ? ["scarf"] : [])]);
  const holding = worn.includes("umbrella");
  const chin: Beard = age === "baby" ? "none" : (beard ?? BY_AGE[age].beard);
  const b = BY_BUILD[build];
  const colour = shirt ? SHIRTS[shirt] : (theme?.palette?.shirt ?? SHIRTS.green);
  const capColour = hat ? HATS[hat] : (theme?.palette?.hat ?? HATS.red);
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
    ...(worn.includes("umbrella") ? [umbrella(age)] : []),
    ...arms(state, mix(colour, -0.08), b, holding),
    {
      kind: "group",
      transform: `matrix(${+(scale * b.head).toFixed(4)} 0 0 ${scale} ${+(256 - 256 * scale * b.head).toFixed(2)} ${
        350 - 236 * scale + bow
      })`,
      children: head(age, chin, capColour, state, worn.includes("sunglasses"), snow),
    },
    ...(worn.includes("scarf") ? [scarf(b, theme?.palette?.scarf)] : []),
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
    viewBox(look.age, view, look.accessories),
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
