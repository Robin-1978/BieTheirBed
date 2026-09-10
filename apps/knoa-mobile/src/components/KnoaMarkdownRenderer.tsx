import { ScrollView, StyleSheet, Text, View, type TextStyle, type ViewStyle } from "react-native";
import { jsx as _jsx } from "react/jsx-runtime";
import { Renderer } from "react-native-marked";

import { colors } from "@/theme";

// 表格列内容不需要屏宽 43% 的固定列宽（marked 默认），改为 flex 均分；
// 列多时每列变窄由内容换行消化，绝大多数表格无需横向滚动。
const MAX_TABLE_COLUMNS = 4;

/**
 * marked 默认给所有文本设置 selectable: true。Android 上文本选择手势会
 * 抢走横向 ScrollView 的滑动（表格右滑失效，且出现系统文字选择高亮）。
 * 聊天输出已有"复制"按钮，这里统一关闭 selectable，让滑动手势通畅。
 */
class KnoaRenderer extends Renderer {
  private tableColumnCount = 0;

  private textNode(children: React.ReactNode, styles?: TextStyle): React.ReactNode {
    return _jsx(Text, { style: styles, children }, this.getKey());
  }

  private viewNode(children: React.ReactNode, styles?: ViewStyle): React.ReactNode {
    return _jsx(View, { style: styles, children }, this.getKey());
  }

  override table(
    header: React.ReactNode[][],
    rows: React.ReactNode[][][],
    tableStyle?: ViewStyle,
    rowStyle?: ViewStyle,
    cellStyle?: ViewStyle,
  ): React.ReactNode {
    this.tableColumnCount = header.length;
    const bordered: ViewStyle = {
      borderWidth: 1,
      borderColor: colors.line,
      borderRadius: 8,
      overflow: "hidden",
      ...tableStyle,
    };
    const content = _jsx(View, {
      style: bordered,
      children: [
        _jsx(View, { style: [styles.headerRow, rowStyle], children: header.map((cell, index) => (
          _jsx(View, {
            style: [styles.cell, this.cellFlexStyle(), cellStyle, this.cellDivider(index, header.length)],
            children: cell,
          }, `h${index}`)
        )) }, "header"),
        rows.map((rowData, rowIndex) => (
          _jsx(View, { style: [styles.row, rowStyle], children: rowData.map((cell, cellIndex) => (
            _jsx(View, {
              style: [
                styles.cell,
                this.cellFlexStyle(),
                cellStyle,
                ...(rowIndex < rows.length - 1 ? [styles.bodyCellDivider] : []),
                this.cellDivider(cellIndex, rowData.length),
              ],
              children: cell,
            }, `${rowIndex}-${cellIndex}`)
          )) }, rowIndex)
        )),
      ],
    }, this.getKey());
    // 少量列时用 flex 均分铺满屏宽；超过阈值才允许横向滚动。
    if (this.tableColumnCount <= MAX_TABLE_COLUMNS) return content;
    return _jsx(ScrollView, { horizontal: true, children: content }, this.getKey());
  }

  private cellFlexStyle(): ViewStyle {
    return this.tableColumnCount > MAX_TABLE_COLUMNS
      ? { width: 160 }
      : { flex: 1 };
  }

  /** 列分隔线：非最后一列在右缘画线（0.5 接近 hairline）。 */
  private cellDivider(index: number, total: number): ViewStyle | null {
    return index < total - 1 ? styles.columnDivider : null;
  }

  // 以下方法仅去掉 selectable，其余与 marked 默认实现一致。
  override paragraph(children: React.ReactNode[], styles?: ViewStyle): React.ReactNode {
    return this.viewNode(children, styles);
  }

  override blockquote(children: React.ReactNode[], styles?: ViewStyle): React.ReactNode {
    return this.viewNode(children, styles);
  }

  override heading(text: string | React.ReactNode[], styles?: TextStyle): React.ReactNode {
    return this.textNode(text, styles);
  }

  override escape(text: string, styles?: TextStyle): React.ReactNode {
    return this.textNode(text, styles);
  }

  override strong(children: string | React.ReactNode[], styles?: TextStyle): React.ReactNode {
    return this.textNode(children, styles);
  }

  override em(children: string | React.ReactNode[], styles?: TextStyle): React.ReactNode {
    return this.textNode(children, styles);
  }

  override codespan(text: string, styles?: TextStyle): React.ReactNode {
    return this.textNode(text, styles);
  }

  override del(children: string | React.ReactNode[], styles?: TextStyle): React.ReactNode {
    return this.textNode(children, styles);
  }

  override text(text: string | React.ReactNode[], styles?: TextStyle): React.ReactNode {
    return this.textNode(text, styles);
  }

  override html(text: string | React.ReactNode[], styles?: TextStyle): React.ReactNode {
    return this.textNode(text, styles);
  }

  override link(
    children: string | React.ReactNode[],
    href: string,
    styles?: TextStyle,
    title?: string | null,
  ): React.ReactNode {
    return _jsx(Text, {
      accessibilityRole: "link",
      accessibilityHint: "Opens in a new window",
      accessibilityLabel: title || "Link",
      onPress: () => {
        void import("react-native").then(({ Linking }) => {
          void Linking.openURL(href).catch(() => undefined);
        });
      },
      style: [styles, styles?.color ? null : { color: colors.accent }],
      children,
    }, this.getKey());
  }
}

const styles = StyleSheet.create({
  headerRow: {
    flexDirection: "row",
    backgroundColor: colors.surfaceMuted,
  },
  row: { flexDirection: "row" },
  cell: {
    paddingHorizontal: 8,
    paddingVertical: 6,
    justifyContent: "center",
  },
  columnDivider: {
    borderRightWidth: 0.5,
    borderRightColor: colors.line,
  },
  bodyCellDivider: {
    borderBottomWidth: 0.5,
    borderBottomColor: colors.line,
  },
});

export function createKnoaRenderer(): KnoaRenderer {
  return new KnoaRenderer();
}
