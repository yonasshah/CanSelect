from django import template
 
register = template.Library()
 
 
@register.filter(name='get_field')
def get_field(form, user_pk):
    """Get a form field by user PK. Usage: {{ form|get_field:member.pk }}"""
    field_name = f'user_{user_pk}'
    if field_name in form.fields:
        return form[field_name]
    return ''
 