import { render } from "@testing-library/react";
import { calculate } from "../../src/core/calc";

describe("pure test", () => {
  it("should calculate", () => {
    expect(calculate(1, 2)).toBe(3);
  });
});
