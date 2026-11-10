import type {Metadata} from 'next';
import MarketMaintenance from '../../components/MarketMaintenance';

export const metadata: Metadata = {
  title: '上市大盤融資維持率｜Taiwan Margin Monitor',
  description: '玩股網排除 ETF 原始維持率、統一公式抽樣核對與個股連算。',
};

export default function MarketPage() {
  return <MarketMaintenance/>;
}
