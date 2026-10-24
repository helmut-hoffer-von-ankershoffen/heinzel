// The Heinzelmännchen mark as a line icon: the favicon's original drawing (pointed hat with its brim and bobble, the
// face, the beard) in one colour, `currentColor`, so it follows the theme's icon colour like the lucide icons.
import type { SVGProps } from "react";

export function HeinzelMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="96 40 320 440"
      width="24"
      height="24"
      fill="none"
      stroke="currentColor"
      strokeWidth={28}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
      {...props}
    >
      <path d="M128 286 C166 200 222 116 286 72 C328 144 366 218 384 286" fill="currentColor" stroke="none" />
      <rect x="112" y="272" width="288" height="36" rx="18" fill="currentColor" stroke="none" />
      <circle cx="286" cy="64" r="22" fill="currentColor" stroke="none" />
      <path d="M182 312 C182 352 214 384 256 384 C298 384 330 352 330 312" />
      <path d="M176 360 C192 462 320 462 336 360" />
      <circle cx="226" cy="336" r="12" fill="currentColor" stroke="none" />
      <circle cx="286" cy="336" r="12" fill="currentColor" stroke="none" />
    </svg>
  );
}
