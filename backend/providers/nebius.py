from .base import ChatAdapter


class NebiusAdapter(ChatAdapter):
    def __init__(self,settings,**kwargs):
        if settings.provider!='nebius':raise ValueError('Nebius settings required.')
        super().__init__(settings,**kwargs)
