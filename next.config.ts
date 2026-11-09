import type {NextConfig} from 'next';
const config: NextConfig = {
  outputFileTracingRoot: process.cwd(),
  ...(process.env.NEXT_PUBLIC_PAGES === '1' && {
    output: 'export',
    basePath: '/taiwan-margin-monitor',
    trailingSlash: true,
  }),
};
export default config;
