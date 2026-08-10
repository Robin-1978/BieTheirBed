import type { ComponentProps } from "react";
import { Pressable, type PressableStateCallbackType, type StyleProp, type ViewStyle } from "react-native";

type Props = Omit<ComponentProps<typeof Pressable>, "style"> & {
  style?: StyleProp<ViewStyle> | ((state: PressableStateCallbackType) => StyleProp<ViewStyle>);
};

export function AppPressable({ style, disabled, android_ripple, ...props }: Props) {
  return (
    <Pressable
      {...props}
      disabled={disabled}
      android_ripple={android_ripple ?? { color: "rgba(47,102,88,0.14)", borderless: false }}
      style={(state) => [
        typeof style === "function" ? style(state) : style,
        state.pressed && !disabled ? pressedStyle : null,
        disabled ? disabledStyle : null,
      ]}
    />
  );
}

const pressedStyle: ViewStyle = { opacity: 0.72, transform: [{ scale: 0.985 }] };
const disabledStyle: ViewStyle = { opacity: 0.52 };
