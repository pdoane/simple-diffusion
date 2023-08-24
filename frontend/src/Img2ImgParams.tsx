import { useSnapshot } from "valtio";
import { SourceImage } from "./SourceImage";
import { CollapsibleContainer } from "./components/Container";
import { FormLabel } from "./components/FormLabel";
import { Slider } from "./components/Slider";
import { Img2ImgParamsState } from "./schema";

interface Img2ImgParamsProps {
  state: Img2ImgParamsState;
}

const SourceParam = ({ state }: Img2ImgParamsProps) => {
  const snap = useSnapshot(state, { sync: true });

  return <SourceImage label="Source" value={snap.source} onChange={(x) => (state.source = x)} />;
};

const NoiseParam = ({ state }: Img2ImgParamsProps) => {
  const snap = useSnapshot(state);

  return (
    <FormLabel label="Noise">
      <Slider value={snap.noise} onChange={(x) => (state.noise = x)} />
    </FormLabel>
  );
};

export const Img2ImgParams = ({ state }: Img2ImgParamsProps) => {
  const snap = useSnapshot(state);

  return (
    <CollapsibleContainer
      label="Image to Image"
      hasSwitch={true}
      isOpen={snap.isOpen}
      isEnabled={snap.isEnabled}
      onIsOpenChange={(x) => (state.isOpen = x)}
      onIsEnabledChange={(x) => (state.isEnabled = x)}
    >
      <SourceParam state={state} />
      <NoiseParam state={state} />
    </CollapsibleContainer>
  );
};
