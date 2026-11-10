import type {Metadata} from 'next';
import MarketMaintenance from '../../components/MarketMaintenance';

export const metadata: Metadata = {
  title: '上市大盤融資維持率｜Taiwan Margin Monitor',
  description: '上市排除 ETF 的資料庫每日重算曲線與玩股網原始維持率。',
};

export default function MarketPage() {
  return <MarketMaintenance/>;
}
