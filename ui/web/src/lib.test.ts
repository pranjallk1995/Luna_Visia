import { describe, expect, it } from "vitest";

import type { ImageRecord } from "./api";
import {
  bulkChanges,
  clickedSelection,
  displayName,
  inBatches,
  inSection,
  matching,
  normalizedTag,
  outcomeMessage,
  pageCount,
  pageOf,
  parsedTags,
  readableName,
  selectionLabel,
  sizeLabel,
  splitName,
  storedName,
  tagCounts,
  tagSlot,
  visibleTags,
  pageSlots,
} from "./lib";

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

describe("readableName", () => {
  it("drops the prefix, extension, and underscores", () => {
    expect(readableName(`${"a".repeat(32)}_Butter_Block.png`)).toBe("Butter Block");
    expect(readableName("Boiled chicken wings_2.png")).toBe("Boiled chicken wings 2");
    expect(readableName("___.png")).toBe("___");
  });
});

describe("visibleTags", () => {
  it("leaves out only the system's ai info tag", () => {
    expect(visibleTags(["cat", "AI  Info", "sofa"])).toEqual(["cat", "sofa"]);
    expect(visibleTags(null)).toEqual([]);
  });
});

describe("tagSlot", () => {
  it("gives one tag one slot, whatever its case or spacing", () => {
    expect(tagSlot("Golden  Hour")).toBe(tagSlot("golden hour"));
    expect(tagSlot("cheese")).toBeGreaterThanOrEqual(0);
    expect(tagSlot("cheese")).toBeLessThan(6);
  });
});

describe("storedName", () => {
  it("adds a prefix displayName removes", () => {
    expect(displayName(storedName("cat.png"))).toBe("cat.png");
  });
});

describe("pageSlots", () => {
  it("shows every page when they fit", () => {
    expect(pageSlots(0, 1)).toEqual([0]);
    expect(pageSlots(2, 7)).toEqual([0, 1, 2, 3, 4, 5, 6]);
  });

  it("keeps seven slots with gaps around the current page", () => {
    expect(pageSlots(0, 17)).toEqual([0, 1, 2, 3, 4, null, 16]);
    expect(pageSlots(3, 17)).toEqual([0, 1, 2, 3, 4, null, 16]);
    expect(pageSlots(4, 17)).toEqual([0, null, 3, 4, 5, null, 16]);
    expect(pageSlots(12, 17)).toEqual([0, null, 11, 12, 13, null, 16]);
    expect(pageSlots(13, 17)).toEqual([0, null, 12, 13, 14, 15, 16]);
    expect(pageSlots(16, 17)).toEqual([0, null, 12, 13, 14, 15, 16]);
  });

  it("always includes the first, last, and current page", () => {
    for (let page = 0; page < 40; page += 1) {
      const slots = pageSlots(page, 40);
      expect(slots).toHaveLength(7);
      for (const wanted of [0, page, 39]) expect(slots).toContain(wanted);
    }
  });
});

describe("clickedSelection", () => {
  const order = ["a", "b", "c", "d", "e"];

  it("toggles one image on a plain click", () => {
    expect([...clickedSelection(new Set(), order, "b", null)]).toEqual(["b"]);
    expect([...clickedSelection(new Set(["b"]), order, "b", null)]).toEqual([]);
  });

  it("adds the range from the anchor in either direction, keeping what was selected", () => {
    expect([...clickedSelection(new Set(["a"]), order, "d", "b")].sort()).toEqual(["a", "b", "c", "d"]);
    expect([...clickedSelection(new Set(), order, "b", "d")].sort()).toEqual(["b", "c", "d"]);
  });

  it("falls back to a toggle when the anchor is no longer in the search", () => {
    expect([...clickedSelection(new Set(), order, "b", "gone")]).toEqual(["b"]);
  });
});

describe("inBatches", () => {
  const send = (log: string[][]) => async (batch: string[]) => {
    log.push(batch);
    return [];
  };

  it("splits items into batches of the given size", async () => {
    const log: string[][] = [];
    const outcome = await inBatches(["a", "b", "c"], 2, (name) => name, send(log));
    expect(log).toEqual([["a", "b"], ["c"]]);
    expect(outcome).toEqual({ done: 3, failed: [] });
  });

  it("collects each image's failure and keeps going", async () => {
    const outcome = await inBatches(["a", "b", "c"], 1, (name) => name, async ([name]) =>
      name === "b" ? [{ name, error: "too many tags" }] : [],
    );
    expect(outcome).toEqual({ done: 2, failed: [{ name: "b", error: "too many tags" }] });
  });

  it("stops at a refused batch and counts the rest as failed", async () => {
    const log: string[][] = [];
    const outcome = await inBatches(["a", "b", "c"], 1, (name) => name, async (batch) => {
      log.push(batch);
      if (batch[0] === "b") throw new Error("backend down");
      return [];
    });
    expect(log).toEqual([["a"], ["b"]]);
    expect(outcome.done).toBe(1);
    expect(outcome.failed).toEqual([
      { name: "b", error: "backend down" },
      { name: "c", error: "backend down" },
    ]);
  });

  it("rejects with the reason when nothing succeeded", async () => {
    await expect(
      inBatches(["a"], 1, (name) => name, async ([name]) => [{ name, error: "not allowed" }]),
    ).rejects.toThrow("not allowed");
  });

  it("does nothing for nothing", async () => {
    await expect(inBatches([], 2, (name: string) => name, send([]))).resolves.toEqual({ done: 0, failed: [] });
  });
});

describe("selection messages", () => {
  it("names the first few images and counts the rest", () => {
    expect(selectionLabel(["a_cat.png"])).toBe("1 image: a cat");
    expect(selectionLabel(["a.png", "b.png", "c.png", "d.png", "e.png"])).toBe("5 images: a, b, c and 2 more");
  });

  it("gives the outcome, with the first reason for any failure", () => {
    expect(outcomeMessage({ done: 2, failed: [] }, "deleted")).toBe("2 images deleted.");
    expect(outcomeMessage({ done: 1, failed: [{ name: "b", error: "Not found." }] }, "hidden")).toBe(
      "1 image hidden. 1 image stayed selected: Not found.",
    );
  });
});

describe("tag helpers", () => {
  it("counts visible tags, most common first", () => {
    const images = [image("a", ["cat", "ai info"]), image("b", ["cat", "dog"])];
    expect(tagCounts(images)).toEqual([
      { tag: "cat", count: 2 },
      { tag: "dog", count: 1 },
    ]);
  });

  it("parses a comma-separated list into normalized, unique tags", () => {
    expect(parsedTags(" Holiday , big   DOG,,holiday")).toEqual(["holiday", "big dog"]);
  });
});

describe("bulkChanges", () => {
  const keep = { mode: "keep" } as const;
  const captioned = (name: string, caption: string, tags: string[] = []) => ({ ...image(name, tags), caption });

  it("adds a tag only where it is missing and leaves out unchanged images", () => {
    const images = [image("a", ["cat"]), image("b", ["dog"])];
    expect(bulkChanges(images, { addTags: ["cat"], removeTags: [], caption: keep })).toEqual([
      { name: "b", add_tags: ["cat"] },
    ]);
  });

  it("removes a tag only where it is present", () => {
    const images = [image("a", ["cat"]), image("b", ["dog"])];
    expect(bulkChanges(images, { addTags: [], removeTags: ["cat"], caption: keep })).toEqual([
      { name: "a", remove_tags: ["cat"] },
    ]);
  });

  it("lets removal win when a tag is both added and removed", () => {
    expect(bulkChanges([image("a", ["cat"])], { addTags: ["cat"], removeTags: ["cat"], caption: keep })).toEqual([
      { name: "a", remove_tags: ["cat"] },
    ]);
  });

  it("finds text in any capitalization and treats both sides literally", () => {
    const images = [captioned("a", "A cost of $5 (paris) in PARIS"), captioned("b", "Berlin")];
    const change = (find: string, replacement: string) =>
      bulkChanges(images, { addTags: [], removeTags: [], caption: { mode: "replace", find, replacement } });
    expect(change("paris", "$1")).toEqual([{ name: "a", caption: "A cost of $5 ($1) in $1" }]);
    expect(change("$5 (", "ten ")).toEqual([{ name: "a", caption: "A cost of ten paris) in PARIS" }]);
    expect(change("", "x")).toEqual([]);
  });

  it("sets one description, skipping blank text and images that already have it", () => {
    const images = [captioned("a", "Old text"), captioned("b", "New text")];
    const change = (text: string) =>
      bulkChanges(images, { addTags: [], removeTags: [], caption: { mode: "set", text } });
    expect(change("  New text ")).toEqual([{ name: "a", caption: "New text" }]);
    expect(change("   ")).toEqual([]);
  });
});
