import { cn } from "@/lib/utils";
import { BrandLogo } from "@/components/shared/brand-logo";

export function Brand({
  className,
  subtitle = true,
  invert = false,
}: {
  className?: string;
  subtitle?: boolean;
  invert?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <BrandLogo id="global-ime" size={36} className="rounded-lg" />
      <div className="leading-tight">
        <div
          className={cn(
            "text-[15px] font-bold tracking-tight",
            invert ? "text-white" : "text-foreground",
          )}
        >
          Global IME
          <span className="text-brand-red">{invert ? "" : " Bank"}</span>
        </div>
        {subtitle && (
          <div
            className={cn(
              "text-[10px] font-medium uppercase tracking-widest",
              invert ? "text-white/60" : "text-muted-foreground",
            )}
          >
            Smart Banking
          </div>
        )}
      </div>
    </div>
  );
}
