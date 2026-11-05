import { Check, ChevronDown } from "lucide-react"
import * as React from "react"
import { Select as Primitive } from "radix-ui"
import { cn } from "@/lib/utils"

const EMPTY = "__pkos_empty_option__"

function Select({ className, children, value, defaultValue, onChange, id, disabled, required, name, ...props }: React.ComponentProps<"select">) {
  const nativeRef = React.useRef<HTMLSelectElement>(null)
  const options = React.Children.toArray(children).filter(
    (child): child is React.ReactElement<React.ComponentProps<"option">> =>
      React.isValidElement(child) && child.type === "option",
  )
  const [internalValue, setInternalValue] = React.useState(String(defaultValue ?? options[0]?.props.value ?? ""))
  const selected = String(value ?? internalValue)
  return (
    <div className="relative w-full">
      <select {...props} ref={nativeRef} name={name} value={selected} disabled={disabled} required={required}
        onChange={onChange} tabIndex={-1} aria-hidden="true" className="hidden">
        {children}
      </select>
      <Primitive.Root value={selected || EMPTY} disabled={disabled} onValueChange={(next) => {
        const resolved = next === EMPTY ? "" : next
        setInternalValue(resolved)
        const native = nativeRef.current
        if (native) {
          const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")?.set
          setter?.call(native, resolved)
          native.dispatchEvent(new Event("change", { bubbles: true }))
        }
      }}>
        <Primitive.Trigger id={id} aria-label={props["aria-label"]} aria-labelledby={props["aria-labelledby"]}
          aria-describedby={props["aria-describedby"]} aria-invalid={props["aria-invalid"]} aria-required={required}
          data-slot="select" className={cn(
            "flex h-11 w-full items-center justify-between gap-3 rounded-full border border-input bg-background px-4 text-base shadow-xs outline-none transition-colors hover:border-ring/60 focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm", className)}>
          <Primitive.Value />
          <Primitive.Icon><ChevronDown className="size-4 text-muted-foreground" /></Primitive.Icon>
        </Primitive.Trigger>
        <Primitive.Portal>
          <Primitive.Content position="popper" sideOffset={6} collisionPadding={12}
            className="z-50 min-w-[var(--radix-select-trigger-width)] max-h-[var(--radix-select-content-available-height)] overflow-hidden rounded-2xl border border-border bg-popover p-1.5 text-popover-foreground shadow-lg">
            <Primitive.ScrollUpButton className="flex justify-center py-1"><ChevronDown className="size-4 rotate-180" /></Primitive.ScrollUpButton>
            <Primitive.Viewport>
              {options.map((option) => {
                const optionValue = String(option.props.value ?? option.props.children ?? "")
                return <Primitive.Item key={optionValue} value={optionValue || EMPTY} disabled={option.props.disabled}
                  className="relative flex min-h-10 cursor-default select-none items-center rounded-xl py-2 pr-9 pl-3 text-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-40">
                  <Primitive.ItemText>{option.props.children}</Primitive.ItemText>
                  <Primitive.ItemIndicator className="absolute right-3"><Check className="size-4" /></Primitive.ItemIndicator>
                </Primitive.Item>
              })}
            </Primitive.Viewport>
            <Primitive.ScrollDownButton className="flex justify-center py-1"><ChevronDown className="size-4" /></Primitive.ScrollDownButton>
          </Primitive.Content>
        </Primitive.Portal>
      </Primitive.Root>
    </div>
  )
}

export { Select }
