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
  accent: semanticColor("accent", "#1F7A5C", "#8FCBB9"),
  accentPressed: semanticColor("accent_pressed", "#1A684E", "#A5D8C9"),
  accentSoft: semanticColor("accent_soft", "#D5EBE1", "#2A453C"),
  accentFaint: semanticColor("accent_faint", "#E9F5F0", "#1D332C"),
  onAccent: semanticColor("on_accent", "#FFFFFF", "#0E2A20"),
  line: semanticColor("line", "#D9D5CC", "#34413C"),
  lineStrong: semanticColor("line_strong", "#C6C2B9", "#485751"),
  danger: semanticColor("danger", "#B4402F", "#F08A7E"),
  dangerSoft: semanticColor("danger_soft", "#FBEAE6", "#452723"),
  warning: semanticColor("warning", "#B4761E", "#E5B96A"),
  warningSoft: semanticColor("warning_soft", "#FAF0DA", "#403524"),
  info: semanticColor("info", "#0473B6", "#58C0F2"),
  infoSoft: semanticColor("info_soft", "#E2F1FA", "#1E3341"),
  success: semanticColor("success", "#1F8A4A", "#5CD98F"),
  successSoft: semanticColor("success_soft", "#E1F3E8", "#20382A"),
  stop: semanticColor("stop", "#52645E", "#8EAAA1"),
  stopSoft: semanticColor("stop_soft", "#E3E9E6", "#2D3B36"),
  overlay: semanticColor("overlay", "#47191F1D", "#99030705"),
  white: "#FFFFFF" as ColorValue,
};

export const radii = { small: 9, medium: 13, large: 17, pill: 999 };
export const spacing = { xsmall: 4, small: 8, medium: 12, large: 16, xlarge: 24 };

export const typography = {
  /** 品牌/启动页主标（“小诺”） */
  brand: { fontSize: 26, fontWeight: "800" as const },
  /** 页面大标题（登录/配对/更新等 hero 场景） */
  display: { fontSize: 22, fontWeight: "700" as const },
  /** 区块标题、卡片大标题、页内主标题 */
  heading: { fontSize: 20, fontWeight: "700" as const },
  /** 小节标题、列表行标题 */
  subheading: { fontSize: 17, fontWeight: "700" as const },
  /** 次级标题、卡片行标题、强调正文 */
  title: { fontSize: 16, fontWeight: "700" as const },
  /** 正文 */
  body: { fontSize: 15, fontWeight: "600" as const },
  /** 辅助说明、元信息 */
  caption: { fontSize: 13, fontWeight: "600" as const },
  /** 次级辅助、时间戳、徽标 */
  small: { fontSize: 12, fontWeight: "600" as const },
  /** 最小允许字号：角标、状态点文字 */
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
