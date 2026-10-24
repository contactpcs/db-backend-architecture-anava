-- 126_assistant_faq_seed.sql
--
-- APPLY ORDER: after 125. Idempotent. Plain data seed, no alembic revision (like 111-116).
--
-- English answers for reference.assistant_faq, generated from Documents/Ask_ANAVA_Design_Brief.md.
-- 208 rows: 151 ACTIVE, 57 DRAFT (is_active = false).
--
-- Left out on purpose: the questions answered from the patient's own records in code (data_answers.py), the
-- safety screens (safety.py) and the one AI-only entry: {'DATA': 29, 'SAFETY': 9, 'AI': 1, 'code': 4}.
--
-- DRAFT rows keep their bracketed gaps ("[Clinic to confirm]") or need doctor sign-off. To publish one: edit the
-- answer, then  UPDATE reference.assistant_faq SET answer = ..., is_active = true, approved_by = <profile id>,
-- approved_at = now() WHERE intent = ...;  A re-run of this file never touches rows that have an approved_by.
--
-- Hindi and Marathi rows are not seeded: translations need native-speaker review. Until then Hindi / Marathi
-- questions are answered by the AI step from these English rows (service._ask_ai), or by the fallback.
--
-- Needs a role that bypasses RLS or is super_admin (reference.assistant_faq write policy): run with the
-- migration login, like the other seed files.

SET client_encoding = 'UTF8';  -- answers contain the rupee sign and dashes; psql on Windows defaults to WIN1252

BEGIN;

INSERT INTO reference."assistant_faq" ("intent", "language", "category", "questions", "answer", "reply_type", "action", "is_active", "sort_order") VALUES
  ('hi', 'en', 'Talking to the assistant', ARRAY['Hi', 'Hello', 'Namaste']::text[], 'Hi {first_name}! I''m the ANAVA assistant. I can help with your sessions, payments, reports and using the app. What would you like to know?', 'faq', NULL, true, 10),
  ('who_are_you_are_you_a_real', 'en', 'Talking to the assistant', ARRAY['Who are you? Are you a real person?']::text[], 'I''m an automated assistant inside the ANAVA app. I''m not a person and not a doctor. For anything about your health, please speak to your doctor.', 'faq', NULL, true, 20),
  ('what_can_you_help_me_with', 'en', 'Talking to the assistant', ARRAY['What can you help me with?']::text[], 'I can tell you about your appointments, payments, refunds, assessments, reports, your clinic, and how to use the app. I can''t give medical advice or contact the clinic for you.', 'faq', NULL, true, 30),
  ('can_i_talk_to_you_in_hindi', 'en', 'Talking to the assistant', ARRAY['Can I talk to you in Hindi or Marathi?']::text[], 'Yes. Choose हिन्दी or मराठी at the top of the chat, or just type in your language.', 'faq', NULL, true, 40),
  ('can_i_speak_instead_of_typing', 'en', 'Talking to the assistant', ARRAY['Can I speak instead of typing?']::text[], 'Yes. Tap the microphone and speak. Your phone turns your voice into text; we don''t store any recording.', 'faq', NULL, true, 50),
  ('can_you_read_your_answer_out_loud', 'en', 'Talking to the assistant', ARRAY['Can you read your answer out loud?']::text[], 'Yes. Tap the speaker icon on any answer to hear it.', 'faq', NULL, true, 60),
  ('can_i_trust_your_answers', 'en', 'Talking to the assistant', ARRAY['Can I trust your answers?']::text[], 'This is a demo assistant and it can make mistakes. For anything important, like fees, timings or your treatment, check the app screens or call your clinic at {clinic_phone}.', 'faq', NULL, true, 70),
  ('can_you_pass_a_message_to_my', 'en', 'Talking to the assistant', ARRAY['Can you pass a message to my doctor?']::text[], 'I can''t send messages to the clinic or your doctor. Please call {clinic_phone}, or ask at your next visit on {next_date}.', 'redirect', NULL, false, 80),
  ('can_i_talk_to_a_human', 'en', 'Talking to the assistant', ARRAY['Can I talk to a human?']::text[], 'I can''t connect you to a person from here. You can call your clinic at {clinic_phone} during [clinic hours].', 'redirect', 'call_clinic', false, 90),
  ('thank_you', 'en', 'Talking to the assistant', ARRAY['Thank you', 'Thanks', 'Bye']::text[], 'You''re welcome, {first_name}. Take care!', 'faq', NULL, true, 100),
  ('show_all_my_upcoming_appointments', 'en', 'My appointments', ARRAY['Show all my upcoming appointments']::text[], 'Here are your upcoming visits.', 'action', 'appointments', true, 110),
  ('why_does_my_session_say_pick_a', 'en', 'My appointments', ARRAY['Why does my session say ''Pick a time''?']::text[], 'Your doctor has planned this session for {date}, but the exact time isn''t chosen yet. Pick a time that suits you.', 'faq', 'pick_time', false, 120),
  ('what_does_payment_due_mean', 'en', 'My appointments', ARRAY['What does ''Payment due'' mean?']::text[], 'Your slot is held for you, but it''s confirmed only after payment. If you don''t pay before the hold ends, the slot is released.', 'faq', 'pay', true, 130),
  ('my_appointment_disappeared_from_the_list', 'en', 'My appointments', ARRAY['My appointment disappeared from the list']::text[], 'It may have been rescheduled, cancelled, or its unpaid hold may have ended. Check Appointments › Past. If you still can''t find it, call {clinic_phone}.', 'faq', NULL, true, 140),
  ('why_does_my_appointment_say_missed', 'en', 'My appointments', ARRAY['Why does my appointment say ''Missed''?']::text[], 'A visit is marked missed when it wasn''t attended at its booked time. You may be able to book a make-up session.', 'faq', 'book', true, 150),
  ('i_see_two_appointments_after_rescheduling_which', 'en', 'My appointments', ARRAY['I see two appointments after rescheduling. Which is right?']::text[], 'The new time is the one in your calendar. The old one is only kept as history, under Payments › Rescheduled.', 'faq', NULL, true, 160),
  ('will_you_remind_me_before_my_session', 'en', 'My appointments', ARRAY['Will you remind me before my session?']::text[], 'The app sends a reminder notification if notifications are on.', 'faq', 'notification_settings', true, 170),
  ('add_my_session_to_my_phone_s', 'en', 'My appointments', ARRAY['Add my session to my phone''s calendar']::text[], 'That isn''t available yet. Your sessions are always in the Appointments calendar in the app.', 'faq', NULL, true, 180),
  ('what_should_i_bring_to_my_appointment', 'en', 'My appointments', ARRAY['What should I bring to my appointment?']::text[], 'Bring your phone with the app, any new reports or prescriptions, and a list of your current medicines. [Clinic to confirm the list]', 'faq', NULL, false, 190),
  ('how_early_should_i_come', 'en', 'My appointments', ARRAY['How early should I come?']::text[], 'Please arrive about [10] minutes before your booked time.', 'faq', NULL, false, 200),
  ('where_is_my_booking_pass', 'en', 'My appointments', ARRAY['Where is my booking pass?', 'Where is my QR code?']::text[], 'After your visit is paid, open it from Home › Details. The QR code there is your booking pass for the clinic desk.', 'faq', NULL, true, 210),
  ('how_do_i_book_an_appointment', 'en', 'Booking', ARRAY['How do I book an appointment?']::text[], 'Tap + on the Appointments screen and choose what you need.', 'action', 'book', true, 220),
  ('book_a_follow_up_for_me', 'en', 'Booking', ARRAY['Book a follow-up for me']::text[], 'I can''t book for you, but I can open booking so you can pick a time.', 'action', 'book', true, 230),
  ('can_i_book_my_first_consultation_in', 'en', 'Booking', ARRAY['Can I book my first consultation in the app?']::text[], 'Yes. Choose Initial consultation, pick a slot and pay to confirm.', 'faq', 'book', true, 240),
  ('are_there_slots_this_saturday', 'en', 'Booking', ARRAY['Are there slots this Saturday?']::text[], 'Open booking to see live free slots for any day.', 'action', 'book', true, 250),
  ('can_i_choose_my_doctor', 'en', 'Booking', ARRAY['Can I choose my doctor?']::text[], 'Your doctor is assigned by the clinic. To ask for a change, please call {clinic_phone}.', 'redirect', NULL, true, 260),
  ('can_i_book_a_device_session_myself', 'en', 'Booking', ARRAY['Can I book a device session myself?']::text[], 'Your doctor plans your device sessions. They appear in Sessions as ''Pick a time'', and you choose the time.', 'faq', NULL, true, 270),
  ('can_i_book_two_sessions_on_the', 'en', 'Booking', ARRAY['Can I book two sessions on the same day?']::text[], '[Clinic to confirm the rule.] The booking screen only shows times you''re allowed to take.', 'faq', NULL, false, 280),
  ('can_i_book_for_my_mother', 'en', 'Booking', ARRAY['Can I book for my mother?', 'Can I book for my child?']::text[], 'Each patient needs their own ANAVA account. A parent or guardian can register a child under 18 and act for them.', 'faq', NULL, true, 290),
  ('why_can_t_i_see_any_free', 'en', 'Booking', ARRAY['Why can''t I see any free slots?']::text[], 'That day may be fully booked or the clinic may be closed. Try another day, or call {clinic_phone}.', 'faq', NULL, true, 300),
  ('how_far_ahead_can_i_book', 'en', 'Booking', ARRAY['How far ahead can I book?']::text[], 'You can book up to [N] days ahead. [Clinic to confirm]', 'faq', NULL, false, 310),
  ('can_i_book_an_online_or_video', 'en', 'Booking', ARRAY['Can I book an online or video consultation?']::text[], '[Clinic to confirm.] At present visits are at the clinic.', 'faq', NULL, false, 320),
  ('i_booked_but_got_no_confirmation', 'en', 'Booking', ARRAY['I booked but got no confirmation']::text[], 'A booking is confirmed only after payment. Check Payments, or open the visit to see its status.', 'faq', NULL, true, 330),
  ('how_do_i_reschedule', 'en', 'Reschedule and cancel', ARRAY['How do I reschedule?']::text[], 'Open the visit and tap Reschedule.', 'action', 'appointment_detail', true, 340),
  ('move_my_session_to_friday', 'en', 'Reschedule and cancel', ARRAY['Move my session to Friday']::text[], 'Open your visit and pick a Friday slot under Reschedule.', 'action', 'reschedule', true, 350),
  ('why_can_t_i_reschedule', 'en', 'Reschedule and cancel', ARRAY['Why can''t I reschedule?']::text[], 'Changes must be made at least {reschedule_min_hours} hours before the visit. For a later change, please call {clinic_phone}.', 'faq', NULL, false, 360),
  ('is_there_a_charge_to_reschedule', 'en', 'Reschedule and cancel', ARRAY['Is there a charge to reschedule?']::text[], '[Clinic to confirm.] Your payment moves to the new time.', 'faq', NULL, false, 370),
  ('how_many_times_can_i_reschedule', 'en', 'Reschedule and cancel', ARRAY['How many times can I reschedule?']::text[], '[Clinic to confirm the limit.]', 'faq', NULL, false, 380),
  ('what_happens_to_my_payment_if_i', 'en', 'Reschedule and cancel', ARRAY['What happens to my payment if I reschedule?']::text[], 'It moves to your new time. You don''t pay again.', 'faq', NULL, true, 390),
  ('where_can_i_see_my_rescheduled_visits', 'en', 'Reschedule and cancel', ARRAY['Where can I see my rescheduled visits?']::text[], 'Payments › Rescheduled shows each visit you moved, from the old time to the new one.', 'faq', NULL, true, 400),
  ('how_do_i_cancel', 'en', 'Reschedule and cancel', ARRAY['How do I cancel?']::text[], 'Open the visit and tap Cancel appointment. Refunds follow the clinic''s policy.', 'action', 'appointment_detail', true, 410),
  ('will_i_get_my_money_back_if', 'en', 'Reschedule and cancel', ARRAY['Will I get my money back if I cancel?']::text[], 'It depends on how early you cancel. See the refund policy.', 'faq', 'refund_policy', true, 420),
  ('i_m_running_late', 'en', 'Reschedule and cancel', ARRAY['I''m running late']::text[], 'Please call the clinic at {clinic_phone} so they can tell you if they can still see you.', 'redirect', NULL, true, 430),
  ('i_can_t_come_today', 'en', 'Reschedule and cancel', ARRAY['I can''t come today']::text[], 'You can reschedule or cancel from the visit. If it''s very close to your time, please call {clinic_phone}.', 'faq', NULL, true, 440),
  ('i_missed_my_session_what_now', 'en', 'Reschedule and cancel', ARRAY['I missed my session. What now?']::text[], 'You can usually book a make-up session within your treatment window.', 'faq', 'book', true, 450),
  ('the_clinic_cancelled_my_appointment_why', 'en', 'Reschedule and cancel', ARRAY['The clinic cancelled my appointment. Why?']::text[], 'I don''t have the reason. Please call {clinic_phone}. If you''d paid, the refund goes back to the account you paid from.', 'redirect', NULL, true, 460),
  ('how_do_i_pay', 'en', 'Payments', ARRAY['How do I pay?']::text[], 'Open the visit and tap Pay securely. You can pay by UPI, card or net banking.', 'action', 'pay', true, 470),
  ('which_payment_methods_do_you_take', 'en', 'Payments', ARRAY['Which payment methods do you take?']::text[], 'UPI, debit or credit cards, and net banking, through Razorpay.', 'faq', NULL, true, 480),
  ('can_i_pay_cash_at_the_clinic', 'en', 'Payments', ARRAY['Can I pay cash at the clinic?']::text[], '[Clinic to confirm.]', 'faq', NULL, false, 490),
  ('is_paying_in_the_app_safe', 'en', 'Payments', ARRAY['Is paying in the app safe?']::text[], 'Yes. Payments go through Razorpay. The ANAVA app never sees or stores your card details.', 'faq', NULL, true, 500),
  ('payment_failed_but_money_was_deducted', 'en', 'Payments', ARRAY['Payment failed but money was deducted']::text[], 'Don''t pay again yet. If the payment didn''t complete, your bank returns the money automatically, usually in 5–7 working days. Check Payments for the status.', 'faq', 'payments', true, 510),
  ('my_payment_is_stuck_on_confirming', 'en', 'Payments', ARRAY['My payment is stuck on ''Confirming''']::text[], 'Sometimes the bank takes a few minutes to confirm. Wait 10 minutes and check Payments again.', 'faq', NULL, true, 520),
  ('i_paid_but_it_still_says_payment', 'en', 'Payments', ARRAY['I paid but it still says Payment due']::text[], 'Pull down to refresh. It can take a few minutes to update. If it still shows due after 30 minutes, call {clinic_phone} with your payment details.', 'faq', NULL, true, 530),
  ('where_is_my_receipt', 'en', 'Payments', ARRAY['Where is my receipt?']::text[], 'Payments › tap Receipt next to the payment to download it.', 'action', 'payments', true, 540),
  ('can_i_get_a_gst_invoice', 'en', 'Payments', ARRAY['Can I get a GST invoice?']::text[], '[Clinic to confirm.]', 'faq', NULL, false, 550),
  ('show_my_payment_history', 'en', 'Payments', ARRAY['Show my payment history']::text[], 'Here is your payment history.', 'action', 'payments', true, 560),
  ('why_is_the_fee_different_from_last', 'en', 'Payments', ARRAY['Why is the fee different from last time?']::text[], 'Fees depend on the type of visit. {type} costs {amount}. [Clinic to confirm any other charges]', 'faq', NULL, false, 570),
  ('do_you_accept_health_insurance', 'en', 'Payments', ARRAY['Do you accept health insurance?']::text[], '[Clinic to confirm.]', 'faq', NULL, false, 580),
  ('can_i_pay_for_all_my_sessions', 'en', 'Payments', ARRAY['Can I pay for all my sessions at once?']::text[], '[Clinic to confirm whether packages are offered.] At present each session is paid separately.', 'faq', NULL, false, 590),
  ('is_emi_available', 'en', 'Payments', ARRAY['Is EMI available?']::text[], '[Clinic to confirm.] Some cards offer EMI on the payment page.', 'faq', NULL, false, 600),
  ('my_slot_hold_ended_while_i_was', 'en', 'Payments', ARRAY['My slot hold ended while I was paying']::text[], 'If money was taken, it is returned automatically. Book the slot again if it''s still free.', 'faq', 'book', true, 610),
  ('i_paid_twice_by_mistake', 'en', 'Payments', ARRAY['I paid twice by mistake']::text[], 'The extra payment should be refunded. Please call {clinic_phone} with both payment details.', 'redirect', NULL, true, 620),
  ('what_is_the_refund_policy', 'en', 'Refunds', ARRAY['What is the refund policy?']::text[], 'Refunds depend on how early you cancel.', 'faq', 'refund_policy', true, 630),
  ('how_long_does_a_refund_take', 'en', 'Refunds', ARRAY['How long does a refund take?']::text[], 'Usually 5–7 working days after the clinic approves it, to the account you paid from.', 'faq', NULL, true, 640),
  ('which_account_will_the_refund_go_to', 'en', 'Refunds', ARRAY['Which account will the refund go to?']::text[], 'The same account, card or UPI you paid from.', 'faq', NULL, true, 650),
  ('why_did_i_get_only_part_of', 'en', 'Refunds', ARRAY['Why did I get only part of my money back?']::text[], 'The amount depends on how close to the visit you cancelled. See the refund policy. [link]', 'faq', NULL, false, 660),
  ('do_i_get_a_refund_for_a', 'en', 'Refunds', ARRAY['Do I get a refund for a missed session?']::text[], '[Clinic to confirm.] See the refund policy. [link]', 'faq', NULL, false, 670),
  ('my_refund_hasn_t_come_after_7', 'en', 'Refunds', ARRAY['My refund hasn''t come after 7 days']::text[], 'Please call {clinic_phone} with your payment date and amount.', 'redirect', NULL, true, 680),
  ('what_is_tdcs', 'en', 'About the treatment (doctor must review these answers)', ARRAY['What is tDCS?']::text[], '[Doctor-approved text.] tDCS is a non-invasive treatment that passes a very weak current through pads placed on your head. It''s done while you''re awake and sitting comfortably.', 'faq', NULL, false, 690),
  ('how_does_the_treatment_work', 'en', 'About the treatment (doctor must review these answers)', ARRAY['How does the treatment work?']::text[], '[Doctor-approved text.] Your doctor sets up a plan of sessions. Each session stimulates chosen areas of the brain for a set time. Your doctor will explain why it suits you.', 'faq', NULL, false, 700),
  ('is_it_safe', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Is it safe?']::text[], '[Doctor-approved text.] It''s generally well tolerated. Your doctor checked it''s right for you before starting. Ask them about any worry.', 'faq', NULL, false, 710),
  ('does_it_hurt', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Does it hurt?']::text[], '[Doctor-approved text.] Most people feel only mild tingling or itching under the pads, which usually fades in a few minutes.', 'faq', NULL, false, 720),
  ('what_will_i_feel_during_a_session', 'en', 'About the treatment (doctor must review these answers)', ARRAY['What will I feel during a session?']::text[], '[Doctor-approved text.] A light tingling at the start is common. Tell the clinical assistant straight away if anything feels uncomfortable.', 'faq', NULL, false, 730),
  ('how_long_is_each_session', 'en', 'About the treatment (doctor must review these answers)', ARRAY['How long is each session?']::text[], '[Clinic to confirm, e.g. 20–30 minutes of stimulation.]', 'faq', NULL, false, 740),
  ('when_will_i_see_results', 'en', 'About the treatment (doctor must review these answers)', ARRAY['When will I see results?']::text[], 'It''s different for everyone. Your doctor tracks your progress with the questionnaires and will discuss it with you.', 'redirect', NULL, true, 750),
  ('is_it_like_electric_shock_treatment', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Is it like electric shock treatment?']::text[], '[Doctor-approved text.] No. tDCS uses a very weak current, you stay awake, and there''s no anaesthesia.', 'faq', NULL, false, 760),
  ('can_i_use_my_phone_during_the', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Can I use my phone during the session?']::text[], 'Yes, you can play the calm games in the app while your session runs.', 'faq', NULL, false, 770),
  ('can_i_stop_if_i_feel_uncomfortable', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Can I stop if I feel uncomfortable?']::text[], 'Yes. Tell the clinical assistant, or tap Need help on the in-session screen.', 'faq', NULL, false, 780),
  ('is_this_treatment_approved', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Is this treatment approved?']::text[], '[Clinic to provide approval and certification details.]', 'faq', NULL, false, 790),
  ('can_my_child_have_tdcs', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Can my child have tDCS?']::text[], 'Only your doctor can decide that. Please ask at a consultation.', 'redirect', NULL, true, 800),
  ('i_m_pregnant_can_i_continue', 'en', 'About the treatment (doctor must review these answers)', ARRAY['I''m pregnant. Can I continue?']::text[], 'Please tell your doctor before your next session. Call {clinic_phone}.', 'redirect', NULL, true, 810),
  ('i_have_a_metal_implant_or_pacemaker', 'en', 'About the treatment (doctor must review these answers)', ARRAY['I have a metal implant or pacemaker']::text[], 'Please tell your doctor before any session. Call {clinic_phone}.', 'redirect', NULL, true, 820),
  ('i_have_epilepsy', 'en', 'About the treatment (doctor must review these answers)', ARRAY['I have epilepsy', 'I have fits']::text[], 'Please make sure your doctor knows before any session. Call {clinic_phone}.', 'redirect', NULL, true, 830),
  ('can_i_do_tdcs_at_home', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Can I do tDCS at home?']::text[], '[Clinic to confirm.] At present sessions are done at the clinic.', 'faq', NULL, false, 840),
  ('will_it_change_my_memory_or_personality', 'en', 'About the treatment (doctor must review these answers)', ARRAY['Will it change my memory or personality?']::text[], 'Please ask your doctor about this. They can explain what to expect for you.', 'redirect', NULL, true, 850),
  ('how_should_i_prepare_for_my_session', 'en', 'Before a session', ARRAY['How should I prepare for my session?']::text[], '[Doctor-approved text.] Wash your hair and don''t apply oil or gel. Eat a light meal, sleep well the night before, and keep taking your usual medicines unless your doctor said otherwise.', 'faq', NULL, false, 860),
  ('can_i_apply_hair_oil', 'en', 'Before a session', ARRAY['Can I apply hair oil?']::text[], 'Please don''t use oil, gel or spray on the day. It stops the pads working well.', 'faq', NULL, false, 870),
  ('should_i_wash_my_hair', 'en', 'Before a session', ARRAY['Should I wash my hair?']::text[], 'Yes, clean dry hair is best.', 'faq', NULL, false, 880),
  ('can_i_eat_before_the_session', 'en', 'Before a session', ARRAY['Can I eat before the session?']::text[], 'Yes, a light meal is fine.', 'faq', NULL, false, 890),
  ('can_i_have_tea_or_coffee_before', 'en', 'Before a session', ARRAY['Can I have tea or coffee before?']::text[], '[Doctor to confirm.]', 'faq', NULL, false, 900),
  ('should_i_take_my_medicines_as_usual', 'en', 'Before a session', ARRAY['Should I take my medicines as usual?']::text[], 'Don''t change your medicines on your own. If you''re unsure, ask your doctor.', 'redirect', NULL, true, 910),
  ('can_i_drive_after_the_session', 'en', 'Before a session', ARRAY['Can I drive after the session?']::text[], '[Doctor to confirm.]', 'faq', NULL, false, 920),
  ('what_should_i_wear', 'en', 'Before a session', ARRAY['What should I wear?']::text[], 'Comfortable clothes. Avoid hair clips, and remove metal accessories from your head.', 'faq', NULL, false, 930),
  ('can_someone_come_with_me', 'en', 'Before a session', ARRAY['Can someone come with me?']::text[], 'Yes, a family member or friend can come along.', 'faq', NULL, false, 940),
  ('i_m_unwell_today_should_i_come', 'en', 'Before a session', ARRAY['I''m unwell today. Should I come?']::text[], 'Please call {clinic_phone} before you leave, and they''ll tell you whether to come.', 'redirect', NULL, true, 950),
  ('my_scalp_is_red_after_the_session', 'en', 'After a session and side effects', ARRAY['My scalp is red after the session']::text[], 'Light redness where the pads were is common and usually fades within a few hours. If you see a blister or burn, or it hurts, call {clinic_phone}.', 'faq', NULL, false, 960),
  ('i_have_a_headache_after_the_session', 'en', 'After a session and side effects', ARRAY['I have a headache after the session']::text[], 'A mild headache can happen and usually passes. If it''s severe or doesn''t go away, call {clinic_phone}. Please mention it in your session feedback.', 'faq', NULL, false, 970),
  ('can_i_take_a_painkiller', 'en', 'After a session and side effects', ARRAY['Can I take a painkiller?']::text[], 'Please ask your doctor or pharmacist before taking any medicine.', 'redirect', NULL, true, 980),
  ('i_feel_tired_after_the_session', 'en', 'After a session and side effects', ARRAY['I feel tired after the session']::text[], 'Feeling a bit tired can happen. Rest. If it''s unusual for you, tell your doctor.', 'faq', NULL, false, 990),
  ('i_can_t_sleep_since_i_started', 'en', 'After a session and side effects', ARRAY['I can''t sleep since I started treatment']::text[], 'Please tell your doctor. Call {clinic_phone} or mention it at your next visit.', 'redirect', NULL, true, 1000),
  ('my_mood_feels_worse', 'en', 'After a session and side effects', ARRAY['My mood feels worse']::text[], 'Thank you for telling me. Please let your doctor know soon. Call {clinic_phone}. If you have any thoughts of harming yourself, help is available right now. [Safety S1 options]', 'redirect', NULL, false, 1010),
  ('is_tingling_normal', 'en', 'After a session and side effects', ARRAY['Is tingling normal?']::text[], 'Yes, mild tingling or itching during the session is common and usually fades.', 'faq', NULL, false, 1020),
  ('how_do_i_report_a_side_effect', 'en', 'After a session and side effects', ARRAY['How do I report a side effect?']::text[], 'Use the feedback form after each session, and tell the clinical assistant at your next visit. If it''s serious, call {clinic_phone}.', 'faq', NULL, false, 1030),
  ('show_my_progress', 'en', 'My treatment progress', ARRAY['Show my progress']::text[], 'Here''s your treatment progress.', 'action', 'sessions', true, 1040),
  ('what_happens_after_my_sessions_finish', 'en', 'My treatment progress', ARRAY['What happens after my sessions finish?']::text[], 'Your doctor reviews your results and publishes a final report. You may be asked to book a follow-up.', 'faq', NULL, true, 1050),
  ('why_did_my_treatment_plan_change', 'en', 'My treatment progress', ARRAY['Why did my treatment plan change?']::text[], 'Your doctor updated your plan. Please ask them at your next visit.', 'redirect', NULL, true, 1060),
  ('can_i_pause_my_treatment', 'en', 'My treatment progress', ARRAY['Can I pause my treatment?']::text[], 'Please talk to your doctor before pausing. Call {clinic_phone}.', 'redirect', NULL, true, 1070),
  ('can_i_have_more_sessions', 'en', 'My treatment progress', ARRAY['Can I have more sessions?']::text[], 'Your doctor decides the number of sessions. Ask at your next visit.', 'redirect', NULL, true, 1080),
  ('what_is_this_questionnaire', 'en', 'Assessments (questionnaires)', ARRAY['What is this questionnaire?']::text[], 'It''s a short set of questions your doctor uses to track how you''re feeling over time.', 'faq', NULL, true, 1090),
  ('why_do_i_have_to_fill_these', 'en', 'Assessments (questionnaires)', ARRAY['Why do I have to fill these?']::text[], 'Your answers show your doctor how your treatment is going.', 'faq', NULL, true, 1100),
  ('how_long_does_it_take', 'en', 'Assessments (questionnaires)', ARRAY['How long does it take?']::text[], 'Most take 5–10 minutes.', 'faq', NULL, true, 1110),
  ('can_i_stop_and_finish_later', 'en', 'Assessments (questionnaires)', ARRAY['Can I stop and finish later?']::text[], 'Yes. Your answers are saved, and next time you continue from where you stopped.', 'faq', NULL, true, 1120),
  ('can_i_change_an_answer', 'en', 'Assessments (questionnaires)', ARRAY['Can I change an answer?']::text[], 'Yes, tap Back before you submit. After submitting, a part can''t be changed.', 'faq', NULL, true, 1130),
  ('i_submitted_by_mistake', 'en', 'Assessments (questionnaires)', ARRAY['I submitted by mistake']::text[], 'Submitted answers can''t be changed in the app. Please tell your doctor at your next visit.', 'redirect', NULL, true, 1140),
  ('can_i_answer_in_hindi_or_marathi', 'en', 'Assessments (questionnaires)', ARRAY['Can I answer in Hindi or Marathi?']::text[], 'Yes. Choose the language at the top of the questionnaire.', 'faq', NULL, true, 1150),
  ('can_the_questions_be_read_aloud', 'en', 'Assessments (questionnaires)', ARRAY['Can the questions be read aloud?']::text[], 'Yes. Tap Read aloud. You can also tap Voice answers and speak your answer.', 'faq', NULL, true, 1160),
  ('which_answer_should_i_choose', 'en', 'Assessments (questionnaires)', ARRAY['Which answer should I choose?', 'Help me answer']::text[], 'There are no right or wrong answers. Choose what''s closest to how you''ve felt. I can''t pick for you.', 'decline', NULL, true, 1170),
  ('why_can_t_i_open_a_finished', 'en', 'Assessments (questionnaires)', ARRAY['Why can''t I open a finished part?']::text[], 'Each part is saved and scored when you finish it, so it can''t be opened again.', 'faq', NULL, true, 1180),
  ('the_assessment_won_t_open', 'en', 'Assessments (questionnaires)', ARRAY['The assessment won''t open']::text[], 'A session assessment opens once your session time has started. Pull down to refresh. If it still won''t open, call {clinic_phone}.', 'faq', NULL, true, 1190),
  ('do_i_have_to_answer_every_question', 'en', 'Assessments (questionnaires)', ARRAY['Do I have to answer every question?']::text[], 'Questions marked optional can be skipped. All others are needed.', 'faq', NULL, true, 1200),
  ('who_sees_my_answers', 'en', 'Assessments (questionnaires)', ARRAY['Who sees my answers?']::text[], 'Your doctor and care team at your clinic.', 'faq', NULL, true, 1210),
  ('what_do_mild_moderate_and_severe_mean', 'en', 'Results and reports', ARRAY['What do Mild, Moderate and Severe mean?']::text[], 'They are bands the questionnaire uses to group scores. They aren''t a diagnosis. Your doctor interprets them.', 'faq', NULL, true, 1220),
  ('what_s_the_difference_between_the_combined', 'en', 'Results and reports', ARRAY['What''s the difference between the combined score and scale scores?']::text[], 'The combined score brings together the latest result of each questionnaire for one condition. Tap it to see each one separately.', 'faq', NULL, true, 1230),
  ('where_is_my_final_report', 'en', 'Results and reports', ARRAY['Where is my final report?']::text[], 'Results › Final reports.', 'action', 'results', true, 1240),
  ('when_will_i_get_my_report', 'en', 'Results and reports', ARRAY['When will I get my report?']::text[], 'Your doctor publishes one after your consultation and when your treatment course ends.', 'faq', NULL, true, 1250),
  ('can_i_share_my_report_with_another', 'en', 'Results and reports', ARRAY['Can I share my report with another doctor?']::text[], '[Feature status: download or share, to be confirmed.]', 'faq', NULL, false, 1260),
  ('explain_my_report_in_simple_words', 'en', 'Results and reports', ARRAY['Explain my report in simple words']::text[], 'Not available in this version. Please ask your doctor to go through it with you.', 'faq', NULL, true, 1270),
  ('do_i_have_depression', 'en', 'Results and reports', ARRAY['Do I have depression?', 'What''s my diagnosis?']::text[], 'I can''t diagnose. Your doctor is the right person to explain your diagnosis.', 'redirect', NULL, true, 1280),
  ('why_can_t_i_see_my_assessment', 'en', 'Results and reports', ARRAY['Why can''t I see my assessment result?']::text[], 'Results appear after you finish all parts and they''re scored. Pull down to refresh.', 'faq', NULL, true, 1290),
  ('what_did_i_sign', 'en', 'Consent', ARRAY['What did I sign?']::text[], 'You can read every consent you''ve signed in Profile › Consents.', 'action', 'profile_consents', true, 1300),
  ('can_i_download_my_signed_consent', 'en', 'Consent', ARRAY['Can I download my signed consent?']::text[], 'Yes, open it in Profile › Consents.', 'action', 'profile_consents', true, 1310),
  ('why_am_i_asked_to_sign_again', 'en', 'Consent', ARRAY['Why am I asked to sign again?']::text[], 'The clinic updated the form, so a new signature is needed.', 'faq', NULL, true, 1320),
  ('can_i_withdraw_my_consent', 'en', 'Consent', ARRAY['Can I withdraw my consent?']::text[], 'Please call {clinic_phone}. Withdrawing may stop your treatment, so the clinic will explain what happens.', 'redirect', NULL, true, 1330),
  ('can_my_guardian_sign_for_me', 'en', 'Consent', ARRAY['Can my guardian sign for me?']::text[], 'Yes, for a patient under 18 the guardian signs on their behalf.', 'faq', NULL, true, 1340),
  ('i_forgot_my_password', 'en', 'Account and sign-in', ARRAY['I forgot my password']::text[], 'Tap Forgot password on the sign-in screen. We''ll send you a 6-digit code.', 'action', 'forgot_password', true, 1350),
  ('how_do_i_change_my_password', 'en', 'Account and sign-in', ARRAY['How do I change my password?']::text[], 'Sign out, then use Forgot password on the sign-in screen to set a new one.', 'faq', NULL, true, 1360),
  ('i_didn_t_get_the_otp', 'en', 'Account and sign-in', ARRAY['I didn''t get the OTP']::text[], 'Wait a minute and tap Resend. Check the number or email is correct, and for email look in spam.', 'faq', NULL, true, 1370),
  ('how_do_i_verify_my_email_or', 'en', 'Account and sign-in', ARRAY['How do I verify my email or phone?']::text[], 'Profile › Verification.', 'action', 'profile_verification', true, 1380),
  ('how_do_i_change_my_phone_number', 'en', 'Account and sign-in', ARRAY['How do I change my phone number or email?']::text[], '[Clinic to confirm the process.] Please call {clinic_phone}.', 'redirect', NULL, false, 1390),
  ('why_was_i_signed_out', 'en', 'Account and sign-in', ARRAY['Why was I signed out?']::text[], 'For your safety you''re signed out after a long time away. Sign in again.', 'faq', NULL, true, 1400),
  ('can_i_use_the_app_on_two', 'en', 'Account and sign-in', ARRAY['Can I use the app on two phones?']::text[], 'Yes, sign in with the same account on both.', 'faq', NULL, true, 1410),
  ('my_account_is_locked_after_wrong_attempts', 'en', 'Account and sign-in', ARRAY['My account is locked after wrong attempts']::text[], 'Wait a few minutes and try again, or reset your password.', 'faq', NULL, true, 1420),
  ('is_there_a_website_version', 'en', 'Account and sign-in', ARRAY['Is there a website version?']::text[], 'Yes, you can also sign in on the ANAVA patient web portal. [link]', 'faq', NULL, false, 1430),
  ('how_do_i_sign_out', 'en', 'Account and sign-in', ARRAY['How do I sign out?']::text[], 'Profile › Sign out.', 'faq', NULL, true, 1440),
  ('i_want_to_delete_my_account', 'en', 'Account and sign-in', ARRAY['I want to delete my account']::text[], 'Profile › Delete my account explains what happens and how to ask.', 'action', 'delete_account', true, 1450),
  ('i_m_clinic_staff_can_i_use', 'en', 'Account and sign-in', ARRAY['I''m clinic staff. Can I use this app?']::text[], 'This app is for patients. Staff use the ANAVA web dashboard.', 'faq', NULL, true, 1460),
  ('how_do_i_update_my_address', 'en', 'Profile and medical files', ARRAY['How do I update my address?']::text[], 'Profile › Personal information › Edit.', 'action', 'profile_personal', true, 1470),
  ('how_do_i_add_an_emergency_contact', 'en', 'Profile and medical files', ARRAY['How do I add an emergency contact?']::text[], 'Profile › Personal information.', 'action', 'profile_personal', true, 1480),
  ('how_do_i_upload_old_reports_or', 'en', 'Profile and medical files', ARRAY['How do I upload old reports or prescriptions?']::text[], 'Profile › Medical history files › Add a file.', 'action', 'profile_files', true, 1490),
  ('what_files_can_i_upload', 'en', 'Profile and medical files', ARRAY['What files can I upload?']::text[], 'PDF, JPG or PNG, up to 25 MB each.', 'faq', NULL, true, 1500),
  ('my_upload_failed', 'en', 'Profile and medical files', ARRAY['My upload failed']::text[], 'Check the file is PDF, JPG or PNG and under 25 MB, then try again on a good connection. Photos in HEIC format need saving as JPG first.', 'faq', NULL, true, 1510),
  ('my_file_says_checking', 'en', 'Profile and medical files', ARRAY['My file says ''Checking''']::text[], 'Every file is safety-scanned first. That takes about a minute.', 'faq', NULL, true, 1520),
  ('my_file_was_rejected', 'en', 'Profile and medical files', ARRAY['My file was rejected']::text[], 'It didn''t pass the safety check. Try saving it again as a PDF or JPG and upload once more.', 'faq', NULL, true, 1530),
  ('can_i_delete_a_file_i_uploaded', 'en', 'Profile and medical files', ARRAY['Can I delete a file I uploaded?']::text[], '[Feature status to be confirmed.] Please call {clinic_phone}.', 'faq', NULL, false, 1540),
  ('who_can_see_my_files', 'en', 'Profile and medical files', ARRAY['Who can see my files?']::text[], 'Only your care team at your clinic.', 'faq', NULL, true, 1550),
  ('how_do_i_change_my_profile_picture', 'en', 'Profile and medical files', ARRAY['How do I change my profile picture?']::text[], 'The app shows an illustrated picture based on your gender. Photo upload isn''t available.', 'faq', NULL, true, 1560),
  ('why_does_it_say_waiting_for_clinic', 'en', 'Registration and approval', ARRAY['Why does it say ''Waiting for clinic approval''?']::text[], 'Your clinic checks every new registration. You''ll get a notification and can use the app as soon as they approve it.', 'faq', NULL, true, 1570),
  ('how_long_does_approval_take', 'en', 'Registration and approval', ARRAY['How long does approval take?']::text[], '[Clinic to confirm, e.g. within one working day.]', 'faq', NULL, false, 1580),
  ('why_must_i_fill_medical_history_and', 'en', 'Registration and approval', ARRAY['Why must I fill medical history and a questionnaire first?']::text[], 'Your doctor needs this before your first consultation. It''s asked only once.', 'faq', NULL, true, 1590),
  ('i_can_t_finish_signing_up', 'en', 'Registration and approval', ARRAY['I can''t finish signing up']::text[], 'Check your details and OTP. If online sign-up isn''t open, ask your clinic to register you.', 'faq', NULL, true, 1600),
  ('it_says_online_sign_up_isn_t', 'en', 'Registration and approval', ARRAY['It says online sign-up isn''t open']::text[], 'Please ask your clinic to register you. They''ll send you a temporary password.', 'faq', NULL, true, 1610),
  ('the_clinic_registered_me_how_do_i', 'en', 'Registration and approval', ARRAY['The clinic registered me. How do I sign in?']::text[], 'Use the temporary password you received by SMS, then set your own password.', 'faq', NULL, true, 1620),
  ('i_chose_the_wrong_clinic_when_signing', 'en', 'Registration and approval', ARRAY['I chose the wrong clinic when signing up']::text[], 'Please call the clinic you want to visit and they''ll help.', 'redirect', NULL, true, 1630),
  ('how_do_i_turn_notifications_on_or', 'en', 'App settings and problems', ARRAY['How do I turn notifications on or off?']::text[], 'Profile › Notification settings.', 'action', 'notification_settings', true, 1640),
  ('i_m_not_getting_notifications', 'en', 'App settings and problems', ARRAY['I''m not getting notifications']::text[], 'Check notifications are on in Profile › Notification settings and in your phone''s settings for ANAVA.', 'faq', NULL, true, 1650),
  ('change_the_notification_sound_or_vibration', 'en', 'App settings and problems', ARRAY['Change the notification sound or vibration']::text[], 'Profile › Notification settings.', 'action', 'notification_settings', true, 1660),
  ('can_i_change_the_app_language', 'en', 'App settings and problems', ARRAY['Can I change the app language?']::text[], 'The assistant and questionnaires work in English, Hindi and Marathi. Other app screens are in English for now.', 'faq', NULL, true, 1670),
  ('how_do_i_change_the_app_colour', 'en', 'App settings and problems', ARRAY['How do I change the app colour?']::text[], 'Profile › App colours: choose Blue or Green.', 'faq', NULL, true, 1680),
  ('the_app_is_slow_or_not_loading', 'en', 'App settings and problems', ARRAY['The app is slow or not loading']::text[], 'Check your internet, then close and reopen the app. Make sure you have the latest version.', 'faq', NULL, true, 1690),
  ('the_app_keeps_crashing', 'en', 'App settings and problems', ARRAY['The app keeps crashing']::text[], 'Update the app from the store and restart your phone. If it continues and you need something urgent, call {clinic_phone}.', 'faq', NULL, true, 1700),
  ('which_phones_does_the_app_work_on', 'en', 'App settings and problems', ARRAY['Which phones does the app work on?']::text[], 'Android 7.0 or newer, and iPhone with iOS 16.4 or newer.', 'faq', NULL, true, 1710),
  ('does_the_app_work_without_internet', 'en', 'App settings and problems', ARRAY['Does the app work without internet?']::text[], 'No, it needs internet to show your latest details.', 'faq', NULL, true, 1720),
  ('how_do_i_update_the_app', 'en', 'App settings and problems', ARRAY['How do I update the app?']::text[], 'Open the Play Store or App Store, search ANAVA and tap Update.', 'faq', NULL, true, 1730),
  ('what_are_the_clinic_timings', 'en', 'My clinic', ARRAY['What are the clinic timings?']::text[], '[Clinic hours, from clinic settings.]', 'faq', NULL, false, 1740),
  ('is_the_clinic_open_on_sunday', 'en', 'My clinic', ARRAY['Is the clinic open on Sunday?']::text[], '[Clinic to confirm.]', 'faq', NULL, false, 1750),
  ('how_do_i_get_to_the_clinic', 'en', 'My clinic', ARRAY['How do I get to the clinic?']::text[], 'Tap the button below to open it.', 'action', 'clinic_map', true, 1760),
  ('is_there_parking', 'en', 'My clinic', ARRAY['Is there parking?']::text[], '[Clinic to confirm.]', 'faq', NULL, false, 1770),
  ('is_the_clinic_wheelchair_accessible', 'en', 'My clinic', ARRAY['Is the clinic wheelchair accessible?']::text[], '[Clinic to confirm.]', 'faq', NULL, false, 1780),
  ('can_i_change_my_doctor', 'en', 'My clinic', ARRAY['Can I change my doctor?']::text[], 'Please call {clinic_phone} to ask.', 'redirect', NULL, true, 1790),
  ('is_there_an_anava_clinic_near_me', 'en', 'My clinic', ARRAY['Is there an ANAVA clinic near me?']::text[], 'See all clinics on anavaclinics.com. [link]', 'faq', NULL, false, 1800),
  ('what_are_the_games_for', 'en', 'In-session games', ARRAY['What are the games for?']::text[], 'They help you relax and pass the time during a session.', 'faq', NULL, true, 1810),
  ('how_do_the_stars_work', 'en', 'In-session games', ARRAY['How do the stars work?']::text[], 'You earn stars as you finish a game. They''re just for fun.', 'faq', NULL, true, 1820),
  ('what_does_the_need_help_button_do', 'en', 'In-session games', ARRAY['What does the Need help button do?']::text[], 'During a session it alerts the clinical assistant in the room straight away.', 'faq', NULL, true, 1830),
  ('is_my_data_safe', 'en', 'Privacy and the assistant', ARRAY['Is my data safe?']::text[], 'Your data is protected and only your care team can see your health records.', 'faq', 'privacy_policy', true, 1840),
  ('who_can_see_my_chats_with_you', 'en', 'Privacy and the assistant', ARRAY['Who can see my chats with you?']::text[], 'Chats are saved so we can improve the assistant. They aren''t sent to your doctor or clinic staff.', 'faq', NULL, true, 1850),
  ('do_you_send_my_data_to_google', 'en', 'Privacy and the assistant', ARRAY['Do you send my data to Google or an AI company?']::text[], 'Only the words of a question I can''t answer myself are sent to an AI service (NVIDIA), without your name, phone or health records.', 'faq', NULL, true, 1860),
  ('how_do_i_delete_my_chat_history', 'en', 'Privacy and the assistant', ARRAY['How do I delete my chat history?']::text[], '[Decision needed: allow ''Clear chat'', or delete with the account only.]', 'faq', NULL, false, 1870),
  ('can_i_stop_using_the_assistant', 'en', 'Privacy and the assistant', ARRAY['Can I stop using the assistant?']::text[], 'Yes. Just don''t open it, or turn off ''Allow AI assistant'' in Profile › Notification settings › Assistant.', 'faq', NULL, true, 1880),
  ('where_is_the_privacy_policy', 'en', 'Privacy and the assistant', ARRAY['Where is the privacy policy?']::text[], 'Tap the button below to open it.', 'action', 'privacy_policy', true, 1890),
  ('where_are_the_terms_of_use', 'en', 'Privacy and the assistant', ARRAY['Where are the terms of use?']::text[], 'Tap the button below to open it.', 'action', 'terms', true, 1900),
  ('should_i_stop_my_medicine', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Should I stop my medicine?']::text[], 'Please don''t stop or change any medicine on your own. Speak to your doctor first.', 'redirect', NULL, true, 1910),
  ('can_i_increase_my_dose', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Can I increase my dose?']::text[], 'Only your doctor can change a dose. Please ask them.', 'redirect', NULL, true, 1920),
  ('which_medicine_is_good_for_sleep_or', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Which medicine is good for sleep or anxiety?']::text[], 'I can''t suggest medicines. Please ask your doctor.', 'redirect', NULL, true, 1930),
  ('can_i_drink_alcohol_during_treatment', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Can I drink alcohol during treatment?']::text[], 'Please ask your doctor. It can affect your treatment.', 'redirect', NULL, true, 1940),
  ('can_i_do_yoga_or_exercise', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Can I do yoga or exercise?']::text[], 'Please check with your doctor what''s right for you.', 'redirect', NULL, true, 1950),
  ('what_should_i_eat', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['What should I eat?']::text[], 'Please ask your doctor for diet advice.', 'redirect', NULL, true, 1960),
  ('is_tdcs_better_than_medicines', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Is tDCS better than medicines?']::text[], 'Your doctor chose your treatment for you and can explain why.', 'redirect', NULL, true, 1970),
  ('can_you_read_my_blood_test', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['Can you read my blood test?']::text[], 'I can''t interpret test results. Please share it with your doctor (you can upload it in Medical history files).', 'redirect', NULL, true, 1980),
  ('what_s_the_weather', 'en', 'Medical advice and off-topic (always redirected)', ARRAY['What''s the weather?', 'What is the cricket score?', 'Write my essay']::text[], 'Sorry, I can only help with ANAVA appointments, payments, reports and the app.', 'decline', NULL, true, 1990),
  ('i_m_nervous_about_my_session', 'en', 'Feelings and safety', ARRAY['I''m nervous about my session']::text[], 'That''s very normal. Sessions are calm, you''re awake, and the clinical assistant stays with you. You can ask them to stop anytime.', 'faq', NULL, true, 2000),
  ('i_m_scared_my_treatment_won_t', 'en', 'Feelings and safety', ARRAY['I''m scared my treatment won''t work']::text[], 'That''s an understandable worry. Please share it with your doctor at your next visit. They can talk you through your progress.', 'redirect', NULL, true, 2010),
  ('give_me_the_doctor_s_personal_number', 'en', 'Unusual messages', ARRAY['Give me the doctor''s personal number']::text[], 'I can''t share staff contact details. You can reach the clinic at {clinic_phone}.', 'decline', NULL, true, 2020),
  ('tell_me_about_another_patient', 'en', 'Unusual messages', ARRAY['Tell me about another patient']::text[], 'I can only show your own information.', 'decline', NULL, true, 2030),
  ('how_do_i_give_feedback', 'en', 'Feedback', ARRAY['How do I give feedback?']::text[], 'After each session you''ll get a notification. Tap it to answer a few quick questions.', 'faq', NULL, true, 2040),
  ('i_missed_the_feedback_notification', 'en', 'Feedback', ARRAY['I missed the feedback notification']::text[], 'Open the completed visit and tap Give feedback. It''s open for [48 hours] after the session.', 'faq', NULL, false, 2050),
  ('can_i_change_my_feedback', 'en', 'Feedback', ARRAY['Can I change my feedback?']::text[], '[Decision needed.] Proposed: no, once sent it''s saved as it is.', 'faq', NULL, false, 2060),
  ('will_the_clinic_reply_to_my_feedback', 'en', 'Feedback', ARRAY['Will the clinic reply to my feedback?']::text[], 'Feedback is collected to improve our service. Nobody reads it live and you won''t get a reply. For anything urgent, please call {clinic_phone}.', 'faq', NULL, true, 2070),
  ('is_my_feedback_anonymous', 'en', 'Feedback', ARRAY['Is my feedback anonymous?']::text[], 'No. It''s linked to your session so we can understand each visit. It stays private to ANAVA.', 'faq', NULL, true, 2080)
ON CONFLICT ("intent", "language") DO UPDATE SET
    "category" = EXCLUDED."category", "questions" = EXCLUDED."questions", "answer" = EXCLUDED."answer",
    "reply_type" = EXCLUDED."reply_type", "action" = EXCLUDED."action", "sort_order" = EXCLUDED."sort_order",
    "updated_at" = now()
    WHERE reference."assistant_faq"."approved_by" IS NULL AND reference."assistant_faq"."approved_at" IS NULL;

COMMIT;


-- DRAFT ROWS (not served until completed and activated)
--   can_you_pass_a_message_to_my: placeholders not supplied yet: next_date
--   can_i_talk_to_a_human: needs clinic facts: clinic hours
--   why_does_my_session_say_pick_a: placeholders not supplied yet: date
--   what_should_i_bring_to_my_appointment: needs clinic facts: Clinic to confirm the list
--   how_early_should_i_come: needs clinic facts: 10
--   can_i_book_two_sessions_on_the: needs clinic facts: Clinic to confirm the rule.
--   how_far_ahead_can_i_book: needs clinic facts: N; Clinic to confirm
--   can_i_book_an_online_or_video: needs clinic facts: Clinic to confirm.
--   why_can_t_i_reschedule: placeholders not supplied yet: reschedule_min_hours
--   is_there_a_charge_to_reschedule: needs clinic facts: Clinic to confirm.
--   how_many_times_can_i_reschedule: needs clinic facts: Clinic to confirm the limit.
--   can_i_pay_cash_at_the_clinic: needs clinic facts: Clinic to confirm.
--   can_i_get_a_gst_invoice: needs clinic facts: Clinic to confirm.
--   why_is_the_fee_different_from_last: needs clinic facts: Clinic to confirm any other charges; placeholders not supplied yet: amount, type
--   do_you_accept_health_insurance: needs clinic facts: Clinic to confirm.
--   can_i_pay_for_all_my_sessions: needs clinic facts: Clinic to confirm whether packages are offered.
--   is_emi_available: needs clinic facts: Clinic to confirm.
--   why_did_i_get_only_part_of: needs clinic facts: link
--   do_i_get_a_refund_for_a: needs clinic facts: Clinic to confirm.; link
--   what_is_tdcs: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   how_does_the_treatment_work: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   is_it_safe: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   does_it_hurt: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   what_will_i_feel_during_a_session: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   how_long_is_each_session: needs clinic facts: Clinic to confirm, e.g. 20–30 minutes of stimulation.; needs doctor sign-off
--   is_it_like_electric_shock_treatment: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   can_i_use_my_phone_during_the: needs doctor sign-off
--   can_i_stop_if_i_feel_uncomfortable: needs doctor sign-off
--   is_this_treatment_approved: needs clinic facts: Clinic to provide approval and certification details.; needs doctor sign-off
--   can_i_do_tdcs_at_home: needs clinic facts: Clinic to confirm.; needs doctor sign-off
--   how_should_i_prepare_for_my_session: needs clinic facts: Doctor-approved text.; needs doctor sign-off
--   can_i_apply_hair_oil: needs doctor sign-off
--   should_i_wash_my_hair: needs doctor sign-off
--   can_i_eat_before_the_session: needs doctor sign-off
--   can_i_have_tea_or_coffee_before: needs clinic facts: Doctor to confirm.; needs doctor sign-off
--   can_i_drive_after_the_session: needs clinic facts: Doctor to confirm.; needs doctor sign-off
--   what_should_i_wear: needs doctor sign-off
--   can_someone_come_with_me: needs doctor sign-off
--   my_scalp_is_red_after_the_session: needs doctor sign-off
--   i_have_a_headache_after_the_session: needs doctor sign-off
--   i_feel_tired_after_the_session: needs doctor sign-off
--   my_mood_feels_worse: needs clinic facts: Safety S1 options
--   is_tingling_normal: needs doctor sign-off
--   how_do_i_report_a_side_effect: needs doctor sign-off
--   can_i_share_my_report_with_another: needs clinic facts: Feature status: download or share, to be confirmed.
--   how_do_i_change_my_phone_number: needs clinic facts: Clinic to confirm the process.
--   is_there_a_website_version: needs clinic facts: link
--   can_i_delete_a_file_i_uploaded: needs clinic facts: Feature status to be confirmed.
--   how_long_does_approval_take: needs clinic facts: Clinic to confirm, e.g. within one working day.
--   what_are_the_clinic_timings: needs clinic facts: Clinic hours, from clinic settings.
--   is_the_clinic_open_on_sunday: needs clinic facts: Clinic to confirm.
--   is_there_parking: needs clinic facts: Clinic to confirm.
--   is_the_clinic_wheelchair_accessible: needs clinic facts: Clinic to confirm.
--   is_there_an_anava_clinic_near_me: needs clinic facts: link
--   how_do_i_delete_my_chat_history: needs clinic facts: Decision needed: allow 'Clear chat', or delete with the account only.
--   i_missed_the_feedback_notification: needs clinic facts: 48 hours
--   can_i_change_my_feedback: needs clinic facts: Decision needed.

-- VERIFY
--  SELECT count(*) FILTER (WHERE is_active), count(*) FILTER (WHERE NOT is_active) FROM reference.assistant_faq;   -- 151, 57
