import { computeTotal } from './app/counter';
import { greet } from '../../shared/greet';

interface Fixture {
  total(n: number): number;
  greet(name: string): string;
  lazy(label: string): Promise<string>;
}

declare global {
  interface Window {
    fixture: Fixture;
  }
}

window.fixture = {
  total: (n: number) => computeTotal([n, n], 3),
  greet: (name: string) => greet(name),
  lazy: async (label: string) => (await import('./lazy/report')).buildReport(label),
};

const boot: number = computeTotal([1, 2, 3], 2);
document.title = `fixture ${boot}`;
