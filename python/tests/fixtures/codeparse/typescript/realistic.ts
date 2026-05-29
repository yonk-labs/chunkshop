export function load(raw: string): number {
  return parseInt(raw, 10);
}

export class Pipeline {
  run(raw: string): number {
    function step(v: number): number {
      return load(v);
    }
    return step(load(raw));
  }
}
