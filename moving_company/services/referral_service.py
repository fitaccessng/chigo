from __future__ import annotations

from datetime import datetime, timedelta
from secrets import token_hex, token_urlsafe

from flask import current_app, session, url_for
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    Booking, Notification, Payment, Referral, ReferralAttribution,
    ReferralCampaign, ReferralCode, ReferralReward, RewardTransaction, User,
)


DEFAULT_CAMPAIGN_ID = 'customer-referrals'


def get_campaign(create=False):
    campaign = ReferralCampaign.query.filter_by(identifier=DEFAULT_CAMPAIGN_ID).first()
    if campaign is None and create:
        campaign = ReferralCampaign(
            identifier=DEFAULT_CAMPAIGN_ID,
            name='Chigo Refer & Earn',
            description='Customer referral programme. Disabled until programme economics are reviewed.',
            active=False,
        )
        db.session.add(campaign)
        db.session.flush()
    return campaign


def campaign_is_active(campaign, at=None):
    at = at or datetime.utcnow()
    return bool(
        campaign and campaign.active
        and (campaign.starts_at is None or campaign.starts_at <= at)
        and (campaign.ends_at is None or campaign.ends_at >= at)
    )


def ensure_referral_code(user):
    if user.role != 'customer':
        return None
    existing = ReferralCode.query.filter_by(user_id=user.id).first()
    if existing:
        return existing
    for _attempt in range(5):
        code = f'CHIGO-{token_hex(8).upper()}'
        if not ReferralCode.query.filter(func.upper(ReferralCode.code) == code).first():
            result = ReferralCode(user_id=user.id, code=code)
            db.session.add(result)
            db.session.flush()
            return result
    raise RuntimeError('Unable to generate a unique referral code. Please try again.')


def referral_link(code):
    origin = (current_app.config.get('CANONICAL_ORIGIN') or '').rstrip('/')
    if not origin:
        origin = url_for('public.home', _external=True).rstrip('/')
    return f'{origin}/?ref={code}'


def capture_referral_attribution(raw_code, source='link'):
    code_text = str(raw_code or '').strip().upper()
    if not code_text or len(code_text) > 24 or session.get('referral_attribution_token'):
        return False
    campaign = get_campaign()
    if not campaign_is_active(campaign):
        return False
    code = ReferralCode.query.filter(
        func.upper(ReferralCode.code) == code_text,
        ReferralCode.is_active.is_(True),
    ).first()
    if code is None:
        return False
    attribution = ReferralAttribution(
        token=token_urlsafe(32), referral_code_id=code.id,
        campaign_id=campaign.id,
        source='manual' if source == 'manual' else 'link',
    )
    db.session.add(attribution)
    db.session.commit()
    session['referral_attribution_token'] = attribution.token
    return True


def attach_attribution_to_user(user, token=None):
    if user.role != 'customer':
        return None
    token = token or session.get('referral_attribution_token')
    if not token:
        return None
    attribution = ReferralAttribution.query.filter_by(token=token).first()
    if attribution is None:
        return None
    campaign = db.session.get(ReferralCampaign, attribution.campaign_id)
    code = db.session.get(ReferralCode, attribution.referral_code_id)
    if not campaign_is_active(campaign, attribution.captured_at) or code is None or not code.is_active:
        return None
    if attribution.captured_at + timedelta(days=campaign.attribution_window_days) < datetime.utcnow():
        return None
    if code.user_id == user.id or User.query.filter_by(id=code.user_id, role='customer').first() is None:
        return None
    if Referral.query.filter_by(referred_user_id=user.id).first():
        return None
    referrer_count = Referral.query.filter_by(referrer_user_id=code.user_id).filter(
        Referral.status != 'rejected'
    ).count()
    if referrer_count >= campaign.max_referrals_per_customer:
        return None
    if Referral.query.filter_by(campaign_id=campaign.id).filter(Referral.status != 'rejected').count() >= campaign.max_referrals_total:
        return None
    referral = Referral(
        referral_code_id=code.id, campaign_id=campaign.id,
        referrer_user_id=code.user_id, referred_user_id=user.id,
        attribution_source=attribution.source, attributed_at=attribution.captured_at,
        status='registered',
        terms_snapshot={
            'reward_amount_kobo': campaign.reward_amount_kobo,
            'discount_amount_kobo': campaign.discount_amount_kobo,
            'minimum_booking_value_kobo': campaign.minimum_booking_value_kobo,
            'minimum_payable_kobo': campaign.minimum_payable_kobo,
            'eligible_services': campaign.eligible_services or [],
            'reward_expiration_days': campaign.reward_expiration_days,
        },
    )
    db.session.add(referral)
    db.session.flush()
    db.session.add(Notification(
        user_id=code.user_id,
        message='A new customer registered through your Chigo referral. A reward is not earned until their eligible move is completed and payment is verified.',
    ))
    session.pop('referral_attribution_token', None)
    return referral


def record_manual_referral(user, raw_code):
    if user.role != 'customer' or user.created_at is None:
        raise ValueError('Referral codes are only available to new customer accounts.')
    campaign = get_campaign()
    if not campaign_is_active(campaign):
        raise ValueError('The referral programme is not currently active.')
    if datetime.utcnow() - user.created_at > timedelta(days=campaign.attribution_window_days):
        raise ValueError('The referral attribution window has expired.')
    if Booking.query.filter_by(customer_id=user.id).count():
        raise ValueError('Referral codes can only be applied before your first booking.')
    if Referral.query.filter_by(referred_user_id=user.id).first():
        raise ValueError('A referral is already linked to this account.')
    if not session.get('referral_attribution_token') and not capture_referral_attribution(raw_code, source='manual'):
        raise ValueError('That referral code is invalid or unavailable.')
    referral = attach_attribution_to_user(user)
    if referral is None:
        db.session.rollback()
        session.pop('referral_attribution_token', None)
        raise ValueError('That referral could not be applied to this account.')
    db.session.commit()
    return referral


def apply_referral_discount(booking, user_id, gross_total):
    referral = Referral.query.filter_by(referred_user_id=user_id).first()
    if referral is None or referral.status not in {'registered', 'booking_qualified'}:
        return 0.0
    if referral.qualifying_booking_id not in (None, booking.id):
        return 0.0
    if Booking.query.filter(
        Booking.customer_id == user_id,
        Booking.id != booking.id,
    ).first():
        return 0.0
    campaign = db.session.get(ReferralCampaign, referral.campaign_id)
    already_qualified = referral.qualifying_booking_id == booking.id
    if not already_qualified:
        if not campaign_is_active(campaign) or referral.attributed_at + timedelta(days=campaign.attribution_window_days) < datetime.utcnow():
            return 0.0
    terms = referral.terms_snapshot or {}
    eligible_services = terms.get('eligible_services') or []
    if booking.primary_service not in eligible_services and eligible_services:
        return 0.0
    gross_amount = int(round(float(gross_total or 0) * 100))
    if gross_amount < int(terms.get('minimum_booking_value_kobo', 0)):
        return 0.0
    minimum_payable = int(terms.get('minimum_payable_kobo', 0))
    maximum_discount = max(0, gross_amount - minimum_payable)
    discount_kobo = min(int(terms.get('discount_amount_kobo', 0)), maximum_discount)
    if discount_kobo <= 0:
        return 0.0
    referral.qualifying_booking_id = booking.id
    referral.status = 'booking_qualified'
    booking.referral_id = referral.id
    previous_discount = int(round(float(booking.referral_discount_amount or 0) * 100))
    booking.referral_discount_amount = discount_kobo / 100
    if discount_kobo != previous_discount:
        db.session.add(RewardTransaction(
            referral_id=referral.id, customer_id=user_id, booking_id=booking.id,
            transaction_type='discount_applied' if previous_discount == 0 else 'discount_adjusted',
            amount_kobo=-discount_kobo if previous_discount == 0 else previous_discount - discount_kobo,
            previous_status='registered' if previous_discount == 0 else 'booking_qualified',
            new_status='booking_qualified',
            description='Server-calculated first-booking referral discount.' if previous_discount == 0 else 'Adjustment to the server-calculated referral discount after quote recalculation.',
            idempotency_key=f'referral:{referral.id}:booking:{booking.id}:discount:{token_hex(12)}',
            processed_at=datetime.utcnow(),
        ))
    return booking.referral_discount_amount


def issue_referral_reward(booking, actor_user_id=None):
    if booking.status != 'Completed' or booking.payment_status != 'Paid' or not booking.referral_id or booking.referral_discount_amount <= 0:
        return None
    if not Payment.query.filter_by(booking_id=booking.id, status='Successful').first():
        return None
    referral = db.session.get(Referral, booking.referral_id)
    if referral is None or referral.qualifying_booking_id != booking.id:
        return None
    existing = ReferralReward.query.filter_by(referral_id=referral.id, reward_type='referrer_credit').first()
    if existing:
        return existing
    if referral.status != 'booking_qualified':
        return None
    now = datetime.utcnow()
    try:
        with db.session.begin_nested():
            reward = ReferralReward(
                referral_id=referral.id, beneficiary_user_id=referral.referrer_user_id,
                booking_id=booking.id, reward_type='referrer_credit',
                amount_kobo=int((referral.terms_snapshot or {}).get('reward_amount_kobo', 0)), status='available',
                idempotency_key=f'referral:{referral.id}:referrer_credit',
                earned_at=now, available_at=now,
                expires_at=now + timedelta(days=int((referral.terms_snapshot or {}).get('reward_expiration_days', 0)))
                if int((referral.terms_snapshot or {}).get('reward_expiration_days', 0)) else None,
            )
            db.session.add(reward)
            db.session.flush()
            db.session.add(RewardTransaction(
                reward_id=reward.id, referral_id=referral.id,
                customer_id=referral.referrer_user_id, booking_id=booking.id,
                transaction_type='credit_issued', amount_kobo=reward.amount_kobo,
                previous_status=None, new_status='available',
                description='Referral reward for a completed, paid move.',
                idempotency_key=f'referral:{referral.id}:credit_issued',
                actor_user_id=actor_user_id, processed_at=now,
            ))
            referral.status = 'rewarded'
            db.session.add(Notification(
                user_id=referral.referrer_user_id, booking_id=booking.id,
                message=f'Your referral reward of ₦{reward.amount_kobo / 100:,.0f} is available as Chigo promotional credit.',
            ))
            db.session.flush()
    except IntegrityError:
        reward = ReferralReward.query.filter_by(referral_id=referral.id, reward_type='referrer_credit').first()
        if reward is None:
            raise
    return reward


def reverse_referral_reward(reward, actor_user_id, reason):
    reason = str(reason or '').strip()
    if reward.status != 'available' or not reason:
        raise ValueError('Only available rewards can be reversed, and a reason is required.')
    if RewardTransaction.query.filter_by(
        reward_id=reward.id, transaction_type='credit_reversed'
    ).first():
        raise ValueError('This reward has already been reversed.')
    previous_status = reward.status
    reward.status = 'reversed'
    referral = db.session.get(Referral, reward.referral_id)
    if referral:
        referral.status = 'reversed'
    db.session.add(RewardTransaction(
        reward_id=reward.id, referral_id=reward.referral_id,
        customer_id=reward.beneficiary_user_id, booking_id=reward.booking_id,
        transaction_type='credit_reversed', amount_kobo=-reward.amount_kobo,
        previous_status=previous_status, new_status=reward.status,
        description=reason[:500],
        idempotency_key=f'referral:{reward.referral_id}:credit_reversed',
        actor_user_id=actor_user_id, processed_at=datetime.utcnow(),
    ))
    db.session.add(Notification(
        user_id=reward.beneficiary_user_id, booking_id=reward.booking_id,
        message='A Chigo referral promotional credit was reversed after review. Contact support if you need details.',
    ))
    return reward


def expire_referral_rewards(now=None):
    now = now or datetime.utcnow()
    expired = ReferralReward.query.filter(
        ReferralReward.status == 'available',
        ReferralReward.expires_at.isnot(None),
        ReferralReward.expires_at <= now,
    ).with_for_update(skip_locked=True).all()
    for reward in expired:
        previous_status = reward.status
        reward.status = 'expired'
        db.session.add(RewardTransaction(
            reward_id=reward.id, referral_id=reward.referral_id,
            customer_id=reward.beneficiary_user_id, booking_id=reward.booking_id,
            transaction_type='credit_expired', amount_kobo=-reward.amount_kobo,
            previous_status=previous_status, new_status=reward.status,
            description='Promotional credit expired under the recorded referral terms.',
            idempotency_key=f'referral:{reward.referral_id}:credit_expired',
            processed_at=now,
        ))
        db.session.add(Notification(
            user_id=reward.beneficiary_user_id, booking_id=reward.booking_id,
            message='A Chigo referral promotional credit has expired under its recorded terms.',
        ))
    return len(expired)


def referral_dashboard_data(user, page=1):
    code = ensure_referral_code(user)
    expire_referral_rewards()
    page = max(1, int(page or 1))
    page_size = 25
    total_referrals = Referral.query.filter_by(referrer_user_id=user.id).count()
    referral_rows = db.session.query(
        Referral, Booking.booking_number, ReferralReward,
    ).outerjoin(Booking, Booking.id == Referral.qualifying_booking_id).outerjoin(
        ReferralReward,
        (ReferralReward.referral_id == Referral.id) & (ReferralReward.reward_type == 'referrer_credit'),
    ).filter(Referral.referrer_user_id == user.id).order_by(
        Referral.created_at.desc(), Referral.id.desc(),
    ).limit(page_size + 1).offset((page - 1) * page_size).all()
    has_next = len(referral_rows) > page_size
    activity = [{
        'referral': referral,
        'booking_reference': booking_reference,
        'reward': reward,
        'direction': 'outgoing',
    } for referral, booking_reference, reward in referral_rows[:page_size]]
    successful_count = Referral.query.filter_by(referrer_user_id=user.id, status='rewarded').count()
    pending_count = Referral.query.filter(
        Referral.referrer_user_id == user.id,
        Referral.status.in_(['registered', 'booking_qualified', 'awaiting_completion']),
    ).count()
    reward_sums = dict(db.session.query(
        ReferralReward.status, func.sum(ReferralReward.amount_kobo),
    ).filter_by(beneficiary_user_id=user.id).group_by(ReferralReward.status).all())
    transaction_sums = dict(db.session.query(
        RewardTransaction.transaction_type, func.sum(RewardTransaction.amount_kobo),
    ).filter_by(customer_id=user.id).group_by(RewardTransaction.transaction_type).all())
    now = datetime.utcnow()
    available_kobo = int(db.session.query(func.coalesce(func.sum(ReferralReward.amount_kobo), 0)).filter(
        ReferralReward.beneficiary_user_id == user.id,
        ReferralReward.status == 'available',
        (ReferralReward.expires_at.is_(None)) | (ReferralReward.expires_at > now),
    ).scalar() or 0)
    pending_kobo = int(reward_sums.get('pending') or 0)
    earned_kobo = sum(int(reward_sums.get(status) or 0) for status in ('available', 'redeemed', 'expired', 'reversed'))
    incoming = Referral.query.filter_by(referred_user_id=user.id).first()
    if incoming:
        qualifying_booking = db.session.get(Booking, incoming.qualifying_booking_id) if incoming.qualifying_booking_id else None
        if qualifying_booking:
            activity.append({
                'referral': incoming,
                'booking_reference': qualifying_booking.booking_number,
                'reward': None,
                'direction': 'incoming',
                'discount_amount': qualifying_booking.referral_discount_amount,
            })
        elif page == 1:
            activity.append({
                'referral': incoming, 'booking_reference': None, 'reward': None,
                'direction': 'incoming', 'discount_amount': 0,
            })
    campaign = get_campaign()
    return {
        'code': code,
        'link': referral_link(code.code),
        'referrals': activity,
        'successful_count': successful_count,
        'pending_count': pending_count,
        'pending_kobo': pending_kobo,
        'available_kobo': available_kobo,
        'earned_kobo': earned_kobo,
        'redeemed_kobo': abs(int(transaction_sums.get('credit_redeemed') or 0)),
        'expired_kobo': int(reward_sums.get('expired') or 0),
        'reversed_kobo': int(reward_sums.get('reversed') or 0),
        'page': page,
        'has_next': has_next,
        'has_previous': page > 1,
        'total_referrals': total_referrals,
        'campaign': campaign,
        'campaign_active': campaign_is_active(campaign),
        'discount_amount_kobo': campaign.discount_amount_kobo if campaign else 200000,
        'can_apply_code': bool(
            campaign_is_active(campaign)
            and user.created_at
            and datetime.utcnow() - user.created_at <= timedelta(days=campaign.attribution_window_days)
            and not Booking.query.filter_by(customer_id=user.id).first()
            and not Referral.query.filter_by(referred_user_id=user.id).first()
        ),
    }