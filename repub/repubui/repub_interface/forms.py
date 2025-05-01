import os
from django import forms
from .models import ProcessingJob


class ProcessingJobForm(forms.ModelForm):
    class Meta:
        model = ProcessingJob
        fields = [
            'title', 'input_file', 'input_type', 'language',
            'crop', 'deskew', 'ocr', 'dewarp', 'draw_contours', 'gray',
            'rotate_type', 'reduce_factor', 'xmax', 'ymax', 'maxcontours'
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'input_file': forms.FileInput(attrs={'class': 'form-control', 'required': 'required'}),
            'input_type': forms.Select(attrs={'class': 'form-select'}),
            'language': forms.TextInput(attrs={'class': 'form-control'}),
            'crop': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'deskew': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'ocr': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'dewarp': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'draw_contours': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'gray': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'rotate_type': forms.Select(attrs={'class': 'form-select'}),
            'reduce_factor': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.1'}),
            'xmax': forms.NumberInput(attrs={'class': 'form-control'}),
            'ymax': forms.NumberInput(attrs={'class': 'form-control'}),
            'maxcontours': forms.NumberInput(attrs={'class': 'form-control'}),
        }
        labels = {
            'input_file': 'Upload File',
            'input_type': 'Input Type',
            'language': 'OCR Language(s) (e.g., eng+fra)',
            'crop': 'Crop images',
            'deskew': 'Deskew images',
            'ocr': 'Apply OCR',
            'dewarp': 'Dewarp images',
            'draw_contours': 'Draw contours (for debugging)',
            'gray': 'Convert to grayscale',
            'rotate_type': 'Rotation calculation method',
            'reduce_factor': 'Reduce image size by factor (optional)',
            'xmax': 'Max horizontal line distance (pixels)',
            'ymax': 'Max vertical line distance (pixels)',
            'maxcontours': 'Maximum contours to examine',
        }
        help_texts = {
            'input_file': 'For PDF input type: upload a PDF file. For Images input type: upload a ZIP file with images or a single image file.',
            'language': 'Use language codes separated by + (e.g., eng+hin for English+Hindi)',
            'reduce_factor': 'Values less than 1 reduce the size (e.g., 0.5 is half size)',
            'draw_contours': 'Only draws contours on the images without other processing - useful for debugging',
            'gray': 'Only converts images to grayscale without other processing',
        }

    def clean_input_file(self):
        input_file = self.cleaned_data.get('input_file')
        if not input_file:
            raise forms.ValidationError("Please select a file to upload.")
        return input_file

    def clean(self):
        cleaned_data = super().clean()
        input_type = cleaned_data.get('input_type')
        input_file = cleaned_data.get('input_file')

        if input_file:
            file_extension = os.path.splitext(input_file.name)[1].lower()

            if input_type == 'pdf' and file_extension != '.pdf':
                self.add_error('input_file', 'Please upload a PDF file when PDF is selected as input type.')

            if input_type == 'images' and file_extension not in ['.zip', '.jpg', '.jpeg', '.png', '.tif', '.tiff']:
                self.add_error('input_file',
                               'Please upload a ZIP file or an image file when Images is selected as input type.')

        # Validate that only one of these options can be enabled at a time
        draw_contours = cleaned_data.get('draw_contours')
        gray = cleaned_data.get('gray')
        crop = cleaned_data.get('crop')
        
        if draw_contours and (gray or crop):
            self.add_error('draw_contours', 'Draw contours cannot be used with other processing options.')
            
        if gray and (draw_contours or crop):
            self.add_error('gray', 'Grayscale conversion cannot be used with other processing options.')

        return cleaned_data