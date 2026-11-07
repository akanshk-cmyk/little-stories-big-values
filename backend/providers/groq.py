from .base import ChatAdapter


class GroqAdapter(ChatAdapter):
    def __init__(self,settings,**kwargs):
        if settings.provider!='groq':raise ValueError('Groq settings required.')
        super().__init__(settings,**kwargs)
