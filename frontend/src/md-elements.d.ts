// JSX typings for the Material web components used in the app.
// With jsx:"react-jsx", TypeScript reads JSX.IntrinsicElements from the
// react/jsx-runtime module - augment every place a tool might look.
import type * as React from "react";

type MdProps = React.DetailedHTMLProps<React.HTMLAttributes<HTMLElement>, HTMLElement> & {
  value?: string | number;
  min?: string | number;
  max?: string | number;
  step?: string | number;
  labeled?: boolean;
  ticks?: boolean;
  selected?: boolean;
  checked?: boolean;
  label?: string;
  disabled?: boolean;
  name?: string;
};

interface MdElements {
  "md-slider": MdProps;
  "md-switch": MdProps;
  "md-outlined-select": MdProps;
  "md-select-option": MdProps;
  "md-filled-button": MdProps;
  "md-outlined-button": MdProps;
  "md-text-button": MdProps;
  "md-checkbox": MdProps;
}

declare module "react/jsx-runtime" {
  namespace JSX {
    interface IntrinsicElements extends MdElements {}
  }
}

declare module "react/jsx-dev-runtime" {
  namespace JSX {
    interface IntrinsicElements extends MdElements {}
  }
}

declare module "react" {
  namespace JSX {
    interface IntrinsicElements extends MdElements {}
  }
}
