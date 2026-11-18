import { pathToFileURL } from 'node:url';
import { join } from 'node:path';

const [root, vitePort, backendPort] = process.argv.slice(2);
const { createServer } = await import(pathToFileURL(join(root, 'node_modules/vite/dist/node/index.js')).href);
const backend = 'http://127.0.0.1:' + backendPort;
const server = await createServer({
  root,
  configFile: join(root, 'vite.config.ts'),
  envDir: false,
  server: {
    host: '127.0.0.1',
    port: Number(vitePort),
    strictPort: true,
    hmr: true,
    watch: {},
    proxy: { '/api/admin': backend, '/__preview': backend, '/healthz': backend },
  },
});
await server.listen();
server.printUrls();
