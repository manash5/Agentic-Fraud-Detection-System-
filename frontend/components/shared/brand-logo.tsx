import Image from "next/image";
import { cn } from "@/lib/utils";

export type BrandLogoId =
  | "global-ime"
  | "esewa"
  | "khalti"
  | "ntc"
  | "ncell";

const ALT: Record<BrandLogoId, string> = {
  "global-ime": "Global IME Bank",
  esewa: "eSewa",
  khalti: "Khalti",
  ntc: "Nepal Telecom",
  ncell: "Ncell",
};

const SRC: Record<BrandLogoId, string> = {
  "global-ime": "/logos/Gibl_logo.png",
  esewa: "/logos/esewa_icon.jpg",
  khalti: "/logos/khalti_icon.png",
  ntc: "/logos/ntc.png",
  ncell: "/logos/Ncell_logo.webp",
};

export function BrandLogo({
  id,
  size = 44,
  className,
}: {
  id: BrandLogoId;
  size?: number;
  className?: string;
}) {
  return (
    <Image
      src={SRC[id]}
      alt={ALT[id]}
      width={size}
      height={size}
      className={cn("shrink-0 object-contain", className)}
      unoptimized
    />
  );
}
