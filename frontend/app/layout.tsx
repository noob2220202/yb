import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "확정픽 스캐너",
  description: "여러 북메이커 배당을 실시간 대조해 무위험 구간과 가치 베팅 엣지를 찾는 대시보드",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
