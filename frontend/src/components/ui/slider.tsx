"use client"

import * as React from "react"
import * as SliderPrimitive from "@radix-ui/react-slider"

import { cn } from "@/lib/utils"

const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root>
>(({ className, ...props }, ref) => {
  const initialValue = Array.isArray(props.value) ? props.value : (Array.isArray(props.defaultValue) ? props.defaultValue : [props.min || 0])

  return (
    <SliderPrimitive.Root
      ref={ref}
      className={cn(
        "relative flex w-full touch-none select-none items-center",
        className
      )}
      {...props}
    >
      <SliderPrimitive.Track className="relative h-2 w-full grow overflow-hidden rounded-full bg-secondary">
        <SliderPrimitive.Range className="absolute h-full bg-primary" />
      </SliderPrimitive.Track>
      {/* We blindly render 2 thumbs if value is array of 2, but we need to be careful if uncontrolled */}
      {/* Actually, relying on props.value is tricky if it changes. */}
      {/* For this specific use case, we know when we pass 2 values. */}
      {/* But for a generic component, this is hard without state. */}
      {/* Let's just always render props.value?.length thumbs if controlled. */}

      {(props.value && Array.isArray(props.value) ? props.value : (props.defaultValue && Array.isArray(props.defaultValue) ? props.defaultValue : [0])).map((_, i) => (
         <SliderPrimitive.Thumb
            key={i}
            className="block h-5 w-5 rounded-full border-2 border-primary bg-background ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50"
         />
      ))}
    </SliderPrimitive.Root>
  )
})
Slider.displayName = SliderPrimitive.Root.displayName

export { Slider }
