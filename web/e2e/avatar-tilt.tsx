// Real component and production CSS: a full-viewport SVG layer must not move the cap's hinge.
import { createRoot } from "react-dom/client";
import { HeinzelAvatar } from "../src/components/HeinzelAvatar";
import { AGES, BUILDS, VIEWS } from "../src/components/heinzel";
import "../src/index.css";

const root = document.getElementById("root");
if (!root) throw new Error("Missing fixture root");
createRoot(root).render(
  <div style={{ display: "grid", gridTemplateColumns: "repeat(9, 140px)", gap: 12, padding: 16 }}>
    {AGES.filter((age) => age !== "baby").flatMap((age) =>
      BUILDS.flatMap((build) =>
        VIEWS.map((view) => (
          <div key={`${age}-${build}-${view}`} data-look={`${age}-${build}-${view}`}>
            <p>{`${age} / ${build} / ${view}`}</p>
            {[false, true].map((layer) => (
              <span
                key={String(layer)}
                data-layer={String(layer)}
                style={{ display: "inline-block", width: 65, height: 100, overflow: "hidden" }}
              >
                <HeinzelAvatar
                  age={age}
                  build={build}
                  view={view}
                  label={layer ? "Theme layer" : "Core cap"}
                  className="h-[100px] w-[65px]"
                  theme={
                    layer
                      ? {
                          assets: {
                            [`hat-${age}`]: `/theme-assets/tilt-fixture/heinzel-hat-${age}.svg?v=0123456789abcdef`,
                          },
                        }
                      : undefined
                  }
                />
              </span>
            ))}
          </div>
        )),
      ),
    )}
  </div>,
);
