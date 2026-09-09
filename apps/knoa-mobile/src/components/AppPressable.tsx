import type { ComponentProps } from "react";
import { Pressable, type PressableStateCallbackType, type StyleProp, type ViewStyle } from "react-native";

/** 触控目标最低尺寸之外的补偿半径：视觉上较小的按钮通过 hitSlop 扩展到 44pt。 */
const DEFAULT_HIT_SLOP = 8;

type Props = Omit<ComponentProps<typeof Pressable>, "style"> & {
  style?: StyleProp<ViewStyle> | ((state: PressableStateCallbackType) => StyleProp<ViewStyle>);
};

export function AppPressable({ style, disabled, android_ripple, hitSlop, ...props }: Props) {
  return (
    <Pressable
      {...props}
      disabled={disabled}
      hitSlop={hitSlop ?? DEFAULT_HIT_SLOP}
      android_ripple={android_ripple ?? { color: "rgba(31,122,92,0.14)", borderless: false }}
      style={(state) => [
        typeof style === "function" ? style(state) : style,
        state.pressed && !disabled ? pressedStyle : null,
        disabled ? disabledStyle : null,
      ]}
    />
  );
}

const pressedStyle: ViewStyle = { opacity: 0.82, transform: [{ scale: 0.99 }] };
const disabledStyle: ViewStyle = { opacity: 0.52 };
