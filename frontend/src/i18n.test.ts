import { describe, expect, it } from "vitest";
import { _dict } from "./i18n";
describe("i18n", () => {
  it("every key has both languages", () => {
    for (const [k, v] of Object.entries(_dict)) { expect(v.en, k).toBeTruthy(); expect(v.ar, k).toBeTruthy(); }
  });
});
