import { describe, expect, it } from "vitest";

import type { ImageRecord } from "./api";
import { displayName, inSection, matching, normalizedTag, pageCount, pageOf, sizeLabel, splitName, storedName } from "./lib";

const image = (name: string, tags: string[] = [], favorite = false): ImageRecord => ({
  name,
  tags,
  favorite,
  size_bytes: 1,
  width: 1,
  height: 1,
  version: 1,
});

describe("displayName", () => {
  it("removes only a 32-character hex upload prefix", () => {
    expect(displayName(`${"a".repeat(32)}_cat_photo.png`)).toBe("cat_photo.png");
    expect(displayName("cat_photo.png")).toBe("cat_photo.png");
    expect(displayName(`${"g".repeat(32)}_cat.png`)).toBe(`${"g".repeat(32)}_cat.png`);
  });
});

describe("sizeLabel", () => {
  it("picks a readable unit", () => {
    expect(sizeLabel(512)).toBe("512 B");
    expect(sizeLabel(2048)).toBe("2 KB");
    expect(sizeLabel(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

describe("splitName", () => {
  it("separates the extension", () => {
    expect(splitName("a.b.png")).toEqual({ stem: "a.b", suffix: ".png" });
    expect(splitName("noext")).toEqual({ stem: "noext", suffix: "" });
  });
});

describe("matching", () => {
  const images = [image("sunset.png", ["Beach"]), image(`${"a".repeat(32)}_dog.jpg`, ["pet"])];

  it("searches display names and tags, ignoring case", () => {
    expect(matching(images, " BEACH ").map((item) => item.name)).toEqual(["sunset.png"]);
    expect(matching(images, "dog")).toHaveLength(1);
    expect(matching(images, "")).toHaveLength(2);
  });

  it("does not match the hidden upload prefix", () => {
    expect(matching(images, "aaaa")).toHaveLength(0);
  });
});

describe("inSection", () => {
  it("limits favorites to favorite images", () => {
    const images = [image("a.png", [], true), image("b.png")];
    expect(inSection(images, "favorites").map((item) => item.name)).toEqual(["a.png"]);
    expect(inSection(images, "view")).toHaveLength(2);
  });
});

describe("paging", () => {
  it("always has one page and slices by page", () => {
    expect(pageCount(0, 12)).toBe(1);
    expect(pageCount(13, 12)).toBe(2);
    expect(pageOf([1, 2, 3], 1, 2)).toEqual([3]);
  });
});

describe("normalizedTag", () => {
  it("ignores case and spacing", () => {
    expect(normalizedTag("  Golden   Hour ")).toBe("golden hour");
  });
});

describe("storedName", () => {
  it("adds a prefix displayName removes", () => {
    expect(displayName(storedName("cat.png"))).toBe("cat.png");
  });
});
