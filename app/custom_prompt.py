import nltk
import tiktoken

from prompt_templates import CONDENSE

class CustomPrompt:
    def __init__(self, text, openai, model):
        self.openai = openai

        self.nltk = nltk
        self.nltk.download('punkt')

        self.text = text
        self.model = model

        max_tokens = self.getModelMaxTokens()

        text_tokens = self.getTextTokens()

        if text_tokens >= (max_tokens / 4):
            self.text = self.split()

    def splitText(self):
        # Tokenize the text into sentences
        sentences = nltk.sent_tokenize(self.text)
        
        # Find the midpoint of the text
        midpoint = len(self.text) // 2
        
        # Initialize variables to keep track of the split
        split_index = 0
        running_length = 0
        
        # Iterate through sentences to find the best split point
        for i, sentence in enumerate(sentences):
            running_length += len(sentence)
            if running_length >= midpoint:
                split_index = i
                break
        
        # Join sentences to form the two halves
        first_half = ' '.join(sentences[:split_index + 1])
        second_half = ' '.join(sentences[split_index + 1:])
        
        return first_half, second_half

    def split(self):
        first_text, second_text = self.splitText()

        first_half = CustomPrompt(first_text, self.openai, self.model)
        first_half.condense()

        second_half = CustomPrompt(second_text, self.openai, self.model)
        second_half.condense()

        return first_half + '\n' + second_half


    def condense(self):
        '''
        Condenses the text using ChatGPT to make it more concise.
        '''
        prompt = CONDENSE.format(self.text)

        response = self.openai.Completion.create(
            engine=self.model,  # Use the appropriate model engine
            prompt=prompt,
            max_tokens=150,
            temperature=0.5
        )

        condensed_text = response.choices[0].text.strip()
        self.text = condensed_text
        return self.text
    
    def getModelMaxTokens(self, model_name):
        # Retrieve the specific model information
        model_info = self.openai.Model.retrieve(model_name)

        # Return the max token size of the specified model
        return model_info['max_tokens']
    
    def getTextTokens(self):
        # Get the encoding for the specified model
        encoding = tiktoken.encoding_for_model(self.model)
        
        # Encode the text to get the tokens
        tokens = encoding.encode(self.text)
        
        return tokens