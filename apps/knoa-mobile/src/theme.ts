import { DynamicColorIOS, Platform, PlatformColor, type ColorValue } from "react-native";

function semanticColor(resource: string, light: string, dark: string): ColorValue {
  if (Platform.OS === "android") return PlatformColor(`@color/knoa_${resource}`);
  if (Platform.OS === "ios") return DynamicColorIOS({ light, dark });
  return light;
}

export const colors = {
  background: semanticColor("background", "#F4F0E8", "#141A18"),
  surface: semanticColor("surface", "#FFFCF6", "#1D2522"),
  surfaceElevated: semanticColor("surface_elevated", "#FFFFFF", "#25302C"),
  surfaceMuted: semanticColor("surface_muted", "#ECE8DF", "#202A26"),
  ink: semanticColor("ink", "#232823", "#F2F0E8"),
  muted: semanticColor("muted", "#626A63", "#A6ADA8"),
  accent: semanticColor("accent", "#2F6658", "#7DBAA8"),
  accentPressed: semanticColor("accent_pressed", "#275548", "#96CCBC"),
  accentSoft: semanticColor("accent_soft", "#DCEAE4", "#284038"),
  accentFaint: semanticColor("accent_faint", "#EDF5F1", "#1C3029"),
  onAccent: semanticColor("on_accent", "#FFFFFF", "#102E24"),
  line: semanticColor("line", "#D9D5CC", "#34413C"),
  lineStrong: semanticColor("line_strong", "#C6C2B9", "#485751"),
  danger: semanticColor("danger", "#9B3E38", "#E59089"),
  dangerSoft: semanticColor("danger_soft", "#F7E9E6", "#402825"),
  warning: semanticColor("warning", "#9B6A27", "#D7A85E"),
  warningSoft: semanticColor("warning_soft", "#F7EEDC", "#3C3323"),
  stop: semanticColor("stop", "#52645E", "#8EAAA1"),
  stopSoft: semanticColor("stop_soft", "#E3E9E6", "#2D3B36"),
  overlay: semanticColor("overlay", "#47191F1D", "#99030705"),
  white: "#FFFFFF" as ColorValue,
};

export const radii = { small: 9, medium: 13, large: 17, pill: 999 };
export const spacing = { xsmall: 4, small: 8, medium: 12, large: 16, xlarge: 24 };

export const typography = {
  heading: { fontSize: 20, fontWeight: "800" as const },
  subheading: { fontSize: 17, fontWeight: "700" as const },
  body: { fontSize: 15, fontWeight: "600" as const },
  caption: { fontSize: 13, fontWeight: "600" as const },
  small: { fontSize: 12, fontWeight: "600" as const },
  tiny: { fontSize: 11, fontWeight: "600" as const },
};

export const shadows = {
  card: {
    shadowColor: "#17231F",
    shadowOpacity: 0.06,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 2,
  },
  floating: {
    shadowColor: "#17231F",
    shadowOpacity: 0.14,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 5,
  },
};
