# A-Eco management health image-to-code QA

- Source visual truth:
  - `/Users/lichenhao/Desktop/安环项目/artifacts/aeco-enterprise-visual-system-option1-20260823-v1/screens/09-client-portal-home-with-health-score-desktop.png`
  - `/Users/lichenhao/Desktop/安环项目/artifacts/aeco-enterprise-visual-system-option1-20260823-v1/screens/10-client-health-score-detail-desktop.png`
  - `/Users/lichenhao/Desktop/安环项目/artifacts/aeco-enterprise-visual-system-option1-20260823-v1/screens/11-client-health-score-detail-mobile.png`
- Implementation routes: `/portal` and `/portal/health`
- Review viewports: desktop `1600×1000`; mobile `390×844`
- Review state: the visual score is available only with the explicit local frontend mock; the real HTTP demo remains fail-closed as `暂不评分`.
- Composite convention: source is left; browser implementation is right.

## Full-view comparison evidence

- Portal home, desktop: `.audit/compare-health-home-desktop-final-v2.png`
- Health detail, desktop: `.audit/compare-health-detail-desktop-final-v2.png`
- Health detail first viewport, mobile: `.audit/compare-health-detail-mobile-final-v2.png`

The in-app browser capture pipeline returned half-density raster content for a one-times CDP capture. Evidence was therefore captured at CDP scale 2 and normalized back to CSS-pixel dimensions before composition. DOM measurements remained `1600px` and `390px`; this is evidence normalization, not application zoom.

## Focused region comparison evidence

- Health score band: `.audit/compare-health-summary-focused-final.png`

The score, status, assessment date, six fixed dimensions, priority colors and report action preserve the selected visual hierarchy. The implementation adds the mandatory customer-safe boundary below the score band.

## Browser findings and fixes

- [fixed P1] The 390px header originally pushed `退出` onto a detached row. It now uses a two-level customer header: brand plus functional menu, then the verified enterprise label. The menu exposes only `首页`, `分析报告` and `退出登录`.
- [fixed P2] Desktop score, report and support panels were visibly denser than the source. Their height, padding and type hierarchy were increased while retaining the existing A-Eco tokens.
- [fixed P2] `智能问答` was still visible in the local visual mock although HTTP QA is not available. It is no longer advertised in either formal or visual-preview navigation; the compatibility route is unchanged.
- [fixed P2] Ant Design's deprecated progress-track property produced a console warning. Both score views now use `railColor`.
- [verified] Mobile `documentElement.scrollWidth === clientWidth === 390`; no horizontal overflow.
- [verified] The mobile menu opens through an actual pointer event and contains the expected two links plus logout.
- [verified] The real HTTP demo renders `暂不评分` and has no `.health-score-line`; the synthetic `60/100` snapshot remains confined to explicit mock mode.

## Intentional visual deviations

- The desktop account area retains an explicit text logout instead of copying the source's decorative avatar and chevron; this avoids implying an unimplemented account menu.
- Mobile content uses readable vertical spacing and therefore continues below the first viewport instead of compressing all dimensions into one screen.
- The orange `测试环境 · 演示数据` badge remains visible in visual-preview evidence. It is a safety signal and is not part of the production-facing design.

## Verification

- Browser: real HTTP client portal plus explicit mock preview, desktop and 390px mobile.
- TypeScript: `src/web/node_modules/.bin/tsc -p src/web/tsconfig.app.json --pretty false` — exit 0.
- Source hygiene: `git diff --check` — exit 0.
- Open P0/P1/P2 visual findings: none.

## Comparison history

- Pass 0: implementation and typecheck only; browser evidence missing, result blocked.
- Pass 1: authenticated browser capture exposed mobile header wrapping and desktop density differences.
- Pass 2: responsive header, navigation boundary and panel density corrected; source-versus-browser composites reviewed together.

final result: passed
