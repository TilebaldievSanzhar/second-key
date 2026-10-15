// Переключатель темы: три состояния, иконка и подпись — не только цвет.
import { useTheme, type Theme } from "../theme";
import { Icon, type IconName } from "./Icon";

const OPTIONS: { value: Theme; label: string; icon: IconName }[] = [
  { value: "system", label: "Как в системе", icon: "monitor" },
  { value: "light", label: "Светлая", icon: "sun" },
  { value: "dark", label: "Тёмная", icon: "moon" },
];

export function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div className="theme-switch" role="radiogroup" aria-label="Тема оформления">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={theme === o.value}
          className={theme === o.value ? "on" : ""}
          title={o.label}
          aria-label={o.label}
          onClick={() => setTheme(o.value)}
        >
          <Icon name={o.icon} size={16} strokeWidth={1.9} />
        </button>
      ))}
    </div>
  );
}
