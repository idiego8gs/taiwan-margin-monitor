import type {Metadata} from 'next';
import Intraday from '../../components/Intraday';

export const metadata: Metadata = {
  title: '盤中即時｜Taiwan Margin Monitor',
  description: '盤中漲跌停與跌停鎖死家數、成交節奏、五檔深度與估計融資維持率。',
};

export default function IntradayPage() {
  return <Intraday/>;
}
