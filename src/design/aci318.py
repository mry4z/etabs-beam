"""Reserved extension point for a future self-contained ACI 318 provider."""


class ACI318Provider:
    def get_beam_design(self, beam_name: str):
        raise NotImplementedError("ACI 318 自研配筋不属于当前 MVP；请使用 EtabsResultProvider。")
