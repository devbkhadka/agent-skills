// Minimal ambient declarations so the fixture compiles without @types/node.
declare const require: (id: string) => any;
declare const process: {
  env: Record<string, string | undefined>;
  stdout: { write(chunk: string): void };
};
