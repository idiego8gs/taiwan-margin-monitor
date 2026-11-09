import './globals.css';
import type {Metadata} from 'next';
export const metadata:Metadata={title:'Taiwan Margin Monitor',description:'台股融資資料研究'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="zh-Hant"><body>{children}</body></html>}
