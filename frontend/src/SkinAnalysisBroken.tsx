import type { ComponentProps } from "react";

import OriginalSkinAnalysis from "./SkinAnalysisLegacy";
import LocalSkinRagDev from "./LocalSkinRagDev";

export * from "./SkinAnalysisLegacy";

type SkinAnalysisProps = ComponentProps<
  typeof OriginalSkinAnalysis
>;

export default function SkinAnalysis(
  props: SkinAnalysisProps
) {
  return (
    <>
      <OriginalSkinAnalysis {...props} />

      {import.meta.env.DEV && <LocalSkinRagDev />}
    </>
  );
}
