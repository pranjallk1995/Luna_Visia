import { describe, expect, it } from "vitest";

import type { ImageRecord } from "./api";
import { SORTS, sortItems } from "./sorting";

describe("gallery sort orders", () => {
  const item = (name: string, size: number, updated: string | null, width = 10, height = 10) =>
    ({ name, size_bytes: size, updated_at: updated, width, height, version: 0 }) as ImageRecord;
  const items = [item("b_cat.png", 300, "2026-01-02T00:00:00Z"), item("a_dog.png", 100, "2026-03-01T00:00:00Z", 100, 100), item("c_owl.png", 200, null)];
  it("keeps the library's similarity order by default", () => {
    expect(sortItems(items, SORTS, "similarity")).toBe(items);
  });
  it("sorts by date, name, size, and pixels", () => {
    const names = (id: string) => sortItems(items, SORTS, id).map((image) => image.name);
    expect(names("newest")).toEqual(["a_dog.png", "b_cat.png", "c_owl.png"]);
    expect(names("oldest")).toEqual(["b_cat.png", "a_dog.png", "c_owl.png"]);
    expect(names("name")).toEqual(["a_dog.png", "b_cat.png", "c_owl.png"]);
    expect(names("largest")).toEqual(["b_cat.png", "c_owl.png", "a_dog.png"]);
    expect(names("pixels")[0]).toBe("a_dog.png");
    expect(items.map((image) => image.name)).toEqual(["b_cat.png", "a_dog.png", "c_owl.png"]);
  });
});
