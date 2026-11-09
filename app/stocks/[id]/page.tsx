import {readFileSync} from 'node:fs';
import StockDetail from '../../../components/StockDetail';

export async function generateStaticParams() {
  if (process.env.NEXT_PUBLIC_PAGES !== '1') return [];
  const root = 'public/data';
  const {snapshot_id} = JSON.parse(readFileSync(root + '/latest.json', 'utf8'));
  const manifest = JSON.parse(readFileSync(`${root}/snapshots/${snapshot_id}/index.json`, 'utf8'));
  return manifest.stocks.map((stock: {security_id: number}) => ({id: 's-' + stock.security_id}));
}

export default async function Page({params}: {params: Promise<{id: string}>}) {
  return <StockDetail id={(await params).id}/>;
}
