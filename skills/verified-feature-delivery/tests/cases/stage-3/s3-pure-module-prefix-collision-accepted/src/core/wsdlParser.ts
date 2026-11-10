import { parseWsdl } from "wsdl-parser";

export function parse(text: string): string {
  return parseWsdl(text);
}
