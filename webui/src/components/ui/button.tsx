import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md border border-transparent text-sm font-medium transition-[color,background-color,border-color,box-shadow,transform] duration-200 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg:not([class*='size-'])]:size-4 shrink-0 [&_svg]:shrink-0 outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive",
  {
    variants: {
      variant: {
        default:
          "border-[#e9b949]/70 bg-gradient-to-b from-[#d69a27] to-[#96600f] text-[#120d05] shadow-[inset_0_1px_0_rgba(255,239,166,0.36),0_0_14px_rgba(221,157,39,0.12)] hover:border-[#ffd978] hover:from-[#e7ad39] hover:to-[#a96c12] hover:shadow-[inset_0_1px_0_rgba(255,245,188,0.45),0_0_18px_rgba(236,180,62,0.24)]",
        destructive:
          "bg-destructive text-white shadow-xs hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60",
        outline:
          "border-[rgba(224,172,69,0.36)] bg-[#111114]/80 text-foreground shadow-xs hover:border-[rgba(245,195,89,0.72)] hover:bg-[#241b0f] hover:text-[#f5cf75] hover:shadow-[0_0_14px_rgba(224,172,69,0.12)]",
        secondary:
          "border-[rgba(184,128,32,0.28)] bg-[#20190f] text-[#e8c879] shadow-xs hover:border-[rgba(230,179,72,0.5)] hover:bg-[#2a2011]",
        ghost:
          "text-[#aaa18e] hover:border-[rgba(224,172,69,0.22)] hover:bg-[#20190f]/80 hover:text-[#f2c866] hover:shadow-[0_0_12px_rgba(224,172,69,0.1)]",
        link: "text-[var(--brand)] underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2 has-[>svg]:px-3",
        sm: "h-8 rounded-md gap-1.5 px-3 has-[>svg]:px-2.5",
        lg: "h-10 rounded-md px-6 has-[>svg]:px-4",
        icon: "size-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot : "button";

  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
