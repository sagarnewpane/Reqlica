import {
  Children,
  isValidElement,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from 'react';
import {
  Select as SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/shadcn/select';
import styles from './Select.module.css';

type Option = { value: string; label: ReactNode; disabled?: boolean };
type Props = Omit<
  ComponentPropsWithoutRef<'button'>,
  'value' | 'defaultValue' | 'onChange'
> & {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  required?: boolean;
  name?: string;
};
const emptyValue = '__reqlica_empty_selection__';
function optionsFrom(children: ReactNode): Option[] {
  return Children.toArray(children).flatMap((child) => {
    if (
      !isValidElement<{
        value?: string | number;
        children?: ReactNode;
        disabled?: boolean;
      }>(child)
    )
      return [];
    if (child.type === 'option')
      return [
        {
          value: String(child.props.value ?? child.props.children ?? ''),
          label: child.props.children,
          disabled: child.props.disabled,
        },
      ];
    return optionsFrom(child.props.children);
  });
}
export default function Select({
  children,
  value,
  defaultValue,
  onValueChange,
  disabled,
  required,
  name,
  className,
  ...props
}: Props) {
  const options = optionsFrom(children);
  const mapValue = (next: string | undefined) =>
    next === '' && options.some((option) => !option.value) ? emptyValue : next;
  return (
    <SelectRoot
      value={mapValue(value)}
      defaultValue={mapValue(defaultValue)}
      disabled={disabled}
      required={required}
      name={name}
      onValueChange={(next) => onValueChange?.(next === emptyValue ? '' : next)}
    >
      <SelectTrigger
        {...props}
        disabled={disabled}
        className={`${styles.select} ${className || ''}`}
      >
        <SelectValue placeholder="Choose an option…" />
      </SelectTrigger>
      <SelectContent position="popper" align="start">
        {options.map((option) => (
          <SelectItem
            key={option.value || emptyValue}
            value={option.value || emptyValue}
            disabled={option.disabled}
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </SelectRoot>
  );
}
