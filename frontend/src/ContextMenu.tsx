/**
 * Right-click menus.
 *
 * Anything you can do to a thing — a project, a source file, a layer on the
 * canvas, an export — is reachable by right-clicking it, and by the "⋯"
 * button beside it for a trackpad or a phone. One host renders whichever
 * menu is open; callers just describe their items. Nothing here knows what
 * the items do.
 *
 * Delete items say "Delete…": the ellipsis is the promise that a question
 * comes before anything is lost.
 */
import { MouseEvent, ReactNode, useEffect, useRef, useState } from "react";

export type MenuItem =
  | {
      label: string;
      hint?: string;
      icon?: ReactNode;
      danger?: boolean;
      disabled?: boolean;
      onSelect: () => void;
    }
  | "separator";

type MenuState = { x: number; y: number; items: MenuItem[]; title?: string } | null;

// A module-level channel rather than a React context: the menu is opened
// from deep inside a dozen components, and threading a provider through all
// of them for one function is more plumbing than the feature.
let listener: ((state: MenuState) => void) | null = null;

/** Open a menu at the pointer. Call from onContextMenu or a button's onClick. */
export function openMenu(event: MouseEvent, items: MenuItem[], title?: string) {
  event.preventDefault();
  event.stopPropagation();
  if (!items.length) return;
  const rect = (event.currentTarget as HTMLElement | null)?.getBoundingClientRect();
  // A real right-click opens at the pointer; the "⋯" button opens under the
  // button, so the menu visibly belongs to it.
  const fromButton = event.type === "click" && rect;
  listener?.({
    x: fromButton ? rect.left : event.clientX,
    y: fromButton ? rect.bottom + 4 : event.clientY,
    items,
    title,
  });
}

export function closeMenu() {
  listener?.(null);
}

/** The "⋯" affordance for people who do not right-click. */
export function MenuButton({
  items,
  title,
  label = "More actions",
}: {
  items: MenuItem[];
  title?: string;
  label?: string;
}) {
  return (
    <button
      type="button"
      className="menu-button"
      title={label}
      aria-label={label}
      aria-haspopup="menu"
      onClick={(e) => openMenu(e, items, title)}
      onContextMenu={(e) => openMenu(e, items, title)}
    >
      ⋯
    </button>
  );
}

/** Rendered once, anywhere in the tree. */
export function ContextMenuHost() {
  const [state, setState] = useState<MenuState>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [focused, setFocused] = useState(0);

  useEffect(() => {
    listener = (next) => {
      setState(next);
      setFocused(0);
    };
    return () => {
      listener = null;
    };
  }, []);

  // Dismiss on anything that is not the menu: a click elsewhere, Escape, a
  // scroll, the window changing size under it.
  useEffect(() => {
    if (!state) return;
    const away = (e: Event) => {
      if (ref.current && e.target instanceof Node && ref.current.contains(e.target)) return;
      setState(null);
    };
    const key = (e: KeyboardEvent) => {
      const items = state.items.filter((i): i is Exclude<MenuItem, "separator"> => i !== "separator");
      if (e.key === "Escape") setState(null);
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setFocused((f) => (f + 1) % items.length);
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setFocused((f) => (f - 1 + items.length) % items.length);
      }
      if (e.key === "Enter") {
        const item = items[focused];
        if (item && !item.disabled) {
          setState(null);
          item.onSelect();
        }
      }
    };
    window.addEventListener("mousedown", away, true);
    window.addEventListener("scroll", away, true);
    window.addEventListener("resize", away);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("mousedown", away, true);
      window.removeEventListener("scroll", away, true);
      window.removeEventListener("resize", away);
      window.removeEventListener("keydown", key);
    };
  }, [state, focused]);

  // Keep the menu on screen: flip it up or left when it would run off.
  useEffect(() => {
    const el = ref.current;
    if (!el || !state) return;
    const { innerWidth, innerHeight } = window;
    const box = el.getBoundingClientRect();
    let { x, y } = state;
    if (x + box.width > innerWidth - 8) x = Math.max(8, innerWidth - box.width - 8);
    if (y + box.height > innerHeight - 8) y = Math.max(8, innerHeight - box.height - 8);
    el.style.left = `${x}px`;
    el.style.top = `${y}px`;
  }, [state]);

  if (!state) return null;
  let index = -1;
  return (
    <div
      ref={ref}
      className="context-menu"
      role="menu"
      style={{ left: state.x, top: state.y }}
      onContextMenu={(e) => e.preventDefault()}
    >
      {state.title && <div className="context-menu-title">{state.title}</div>}
      {state.items.map((item, i) => {
        if (item === "separator") return <div key={`sep-${i}`} className="context-menu-sep" role="separator" />;
        index += 1;
        const at = index;
        return (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            className={`${item.danger ? "danger" : ""}${at === focused ? " focused" : ""}`}
            disabled={item.disabled}
            onMouseEnter={() => setFocused(at)}
            onClick={() => {
              setState(null);
              item.onSelect();
            }}
          >
            {item.icon && <span className="context-menu-icon">{item.icon}</span>}
            <span>{item.label}</span>
            {item.hint && <small>{item.hint}</small>}
          </button>
        );
      })}
    </div>
  );
}
