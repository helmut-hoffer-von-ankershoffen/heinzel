// A bookmark's icon: the small image its site showed (kept by hz per origin), else a globe. Never a favicon service.
import { Globe } from "lucide-react";
import { useState } from "react";

const DATA_IMAGE = /^data:image\/(?:png|jpeg|gif|x-icon);base64,[A-Za-z0-9+/=]+$/;

export function Favicon({ image, className = "size-4" }: Readonly<{ image?: string | null; className?: string }>) {
  const [broken, setBroken] = useState<string | null>(null);
  if (!image || !DATA_IMAGE.test(image) || broken === image)
    return <Globe aria-hidden data-favicon="fallback" className={`${className} shrink-0`} />;
  return (
    <img
      src={image}
      alt=""
      aria-hidden
      data-favicon="image"
      className={`${className} shrink-0 rounded-sm`}
      onError={() => setBroken(image)}
    />
  );
}
