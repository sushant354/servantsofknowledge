import logging 

from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.urls import reverse
from .forms import UserRegistrationForm
from django.template.loader import render_to_string
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from rest_framework.authtoken.models import Token

from .models import UserProfile

# Set up logger for this module
logger = logging.getLogger('repubui.users')
def register(request):
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = False  # User cannot login until email is confirmed
            user.save()
            
            # Create user profile
            profile, created = UserProfile.objects.get_or_create(user=user)
            
            # Send activation email
            send_activation_email(request, user)
            
            messages.success(request, 'Registration successful! Please check your email to activate your account.')
            return redirect('login')
    else:
        form = UserRegistrationForm()
    
    return render(request, 'registration/register.html', {'form': form})


def send_activation_email(request, user):
    """Send activation email to user"""
    token = default_token_generator.make_token(user)
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    
    activation_link = request.build_absolute_uri(
        reverse('activate_account', kwargs={'uidb64': uid, 'token': token})
    )
    
    subject = 'Activate Your REPUB Account'
    message = render_to_string('registration/activation_email.txt', {
        'user': user,
        'activation_link': activation_link,
    })
    
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=False,
    )


def activate_account(request, uidb64, token):
    """Activate user account via email link"""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    
    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        
        # Update user profile
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.email_confirmed = True
        profile.save()
        
        messages.success(request, 'Your account has been activated successfully! You can now log in.')
        return redirect('login')
    else:
        messages.error(request, 'The activation link is invalid or has expired.')
        return redirect('register')

@login_required
def api_token_management(request):
    """Manage user's API token for REST framework authentication"""
    token = Token.objects.filter(user=request.user).first()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'generate':
            if not token:
                token = Token.objects.create(user=request.user)
                messages.success(request, 'API token has been generated successfully!')
                logger.info(f"Generated new API token for user {request.user.username}")
            else:
                messages.info(request, 'You already have an API token. Use "Regenerate" to create a new one.')

        elif action == 'regenerate':
            if token:
                token.delete()
            token = Token.objects.create(user=request.user)
            messages.success(request, 'API token has been regenerated successfully! Make sure to update any applications using the old token.')
            logger.info(f"Regenerated API token for user {request.user.username}")

        elif action == 'delete':
            if token:
                token.delete()
                token = None
                messages.success(request, 'API token has been deleted successfully.')
                logger.info(f"Deleted API token for user {request.user.username}")
            else:
                messages.info(request, 'No API token to delete.')

        return redirect('api_token')

    context = {
        'token': token,
    }

    return render(request, 'repub_interface/api_token.html', context)

def authenticate_user(request):
    """Custom authentication that supports both session and token auth"""
    # First try session authentication (for web users)
    if request.user.is_authenticated:
        return request.user
    
    # Try token authentication (for API clients)
    auth_header = request.META.get('HTTP_AUTHORIZATION')
    if auth_header and auth_header.startswith('Token '):
        token = auth_header.split(' ')[1]
        try:
            token_obj = Token.objects.get(key=token)
            return token_obj.user
        except Token.DoesNotExist:
            pass
    
    return None


def login_or_token_required(view_func):
    """Decorator that requires either session login or valid token"""
    def wrapper(request, *args, **kwargs):
        user = authenticate_user(request)
        if user:
            # Set the user on the request for the view
            if not request.user.is_authenticated:
                request.user = user
            return view_func(request, *args, **kwargs)
        else:
            from django.contrib.auth.views import redirect_to_login
            from django.http import JsonResponse
            
            # For API requests, return JSON error
            if request.META.get('HTTP_AUTHORIZATION'):
                return JsonResponse({'success': False, 'error': 'Invalid token'}, status=401)
            
            # For web requests, redirect to login
            return redirect_to_login(request.get_full_path())
    
    return wrapper


