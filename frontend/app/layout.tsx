import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "헤지 박스 빌더",
  description: "한 경기 두 다리 헤지 배팅을 계산해 텔레그램으로 내보내는 관리자 도구",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
