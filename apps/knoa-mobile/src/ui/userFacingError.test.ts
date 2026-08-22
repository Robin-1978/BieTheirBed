import { describe, expect, it } from "vitest";
import { userFacingError } from "./userFacingError";

describe("userFacingError", () => {
  it("maps transport failures to actionable copy", () => {
    expect(userFacingError({ message: "Hub request timed out", kind: "timeout" }, "加载失败")).toContain("超时");
    expect(userFacingError({ message: "Failed to fetch", kind: "network" }, "加载失败")).toContain("网络");
  });
  it("hides local runtime paths", () => {
    expect(userFacingError(new Error("Runtime failed at C:\\ProgramData\\Knoa\\venv"), "请检查运行环境")).toBe("请检查运行环境");
  });
  it("keeps short, useful domain errors", () => {
    expect(userFacingError(new Error("需要先连接电脑"), "加载失败")).toBe("需要先连接电脑");
  });
});
