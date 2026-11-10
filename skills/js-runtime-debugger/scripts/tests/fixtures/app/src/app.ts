import { failHard, priceOrder } from './pricing';

const http = require('node:http');

let requests = 0;

function handle(req: { url?: string }, res: { end(body: string): void }): void {
  requests++;
  const url = new URL(req.url ?? '/', 'http://localhost');
  if (url.pathname === '/order') {
    const qty = Number(url.searchParams.get('qty') ?? '1');
    const quote = priceOrder(url.searchParams.get('sku') ?? 'sku-1', qty);
    res.end(JSON.stringify(quote));
    return;
  }
  if (url.pathname === '/report') {
    const { buildReport } = require('./lazy/report');
    res.end(JSON.stringify(buildReport(requests)));
    return;
  }
  if (url.pathname === '/boom') {
    try {
      failHard('requested');
    } catch (err) {
      res.end(JSON.stringify({ caught: String(err) }));
    }
    return;
  }
  res.end(JSON.stringify({ ok: true, requests }));
}

const server = http.createServer(handle);
server.listen(Number(process.env.PORT ?? 0), '127.0.0.1', () => {
  process.stdout.write(`listening ${server.address().port}\n`);
});
